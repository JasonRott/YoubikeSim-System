"""多日模擬 + 夜間區間調度（§42；Model B 整備到集散場，§42.5）。

串接：Day_d 從(站點快照+集散場初始) → P7 白天(scenario) → 抽 24:00 站點&集散場態
→ 夜間區間調度 → 隔天起跑態 → … N 天。量每日優良是否跨日維持，並把夜間母車成本併入同一 SC 分母。

★ Model B（使用者定案，§42.5）：夜間母車**從過滿站點回收車、送到「該補的集散場」**（不直接補到站）；
   白天 P7 再從補滿的集散場補到站。站點全模式 carry-forward；各模式只差「集散場 04:00 有多滿」。

四模式（--overnight）：
  none         不整備：站點&集散場都 24:00 結轉（集散場會被白天抽乾→隔天空）。誠實下界。
  virtual      組員 policy：回收過滿站點餘量→補車區集散場（貪婪最近對，守恆），計母車成本。可部署。
  full         完美集散場調度：集散場直接補到 CSV 理想目標、站點過滿回收到位（免費）。集散場層上界。
  full_station 站點完美還原：站點還原到真實 04:00 快照、集散場用守恆預設。絕對天花板（站級）參考線。

集散場目標（CSV `district_morning_peak_net_flow.csv`）：補車區(net>0)集散場 = net_flow_d（晨峰儲備）、抽車區=0。
母車：容量100、30km/h、裝卸 3+0.5n 分/訪（取+卸兩訪）；夜窗 00:00→04:00=240分；整班 4hr 計薪夜班 NT$400/hr；
      母車數=ceil(作業工時/240)；併入白天同一 DispatchingCost 分母。

用法：python scripts/run_multiday.py --days 30 --week-mode realweek --overnight virtual --trucks 8
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCEN = PROJECT_ROOT / "scenarios" / "real_system_scenario.py"
REPORT = PROJECT_ROOT / "report"
NETFLOW_CSV = PROJECT_ROOT / "data/derived/district_morning_peak_net_flow.csv"
START_MIN, DUTY, HOURS, SEED_BASE = 240, "240-1440", 20.0, 20260602
SMOOTHED_OD = "data/processed/transition_matrices_smoothed"

# ---- 夜間母車成本參數（§42.2/42.5 定案）----
MT_CAPACITY = 100
MT_SPEED_KMH = 30.0
MT_WINDOW_MIN = 240.0       # 夜窗 00:00–04:00；同時當整班計薪時數(4hr)
C_LABOR_NIGHT = 400.0
C_KM = 8.0
C_TRIP = 50.0
HANDLE_FIX = 3.0
HANDLE_PER_BIKE = 0.5


def km(a, b):
    ml = math.radians((a[0] + b[0]) / 2.0)
    return math.hypot((b[1] - a[1]) * 111320.0 * math.cos(ml), (b[0] - a[0]) * 110540.0) / 1000.0


def day_profile(day: int, week_mode: str, base_profile: str) -> str:
    if week_mode == "single":
        return base_profile
    return "weekend" if (day - 1) % 7 in (5, 6) else "weekday"


def load_static():
    sp = json.load(open(PROJECT_ROOT / "data/processed/visualization_inputs/station_positions.json", encoding="utf-8"))
    cap = json.load(open(PROJECT_ROOT / "data/processed/station_capacity/station_capacity.json", encoding="utf-8"))
    s2d = {sid: pos["district"] for sid, pos in sp.items()}
    caps = {sid: int(info["capacity"]) for sid, info in cap.items()}
    pts = defaultdict(list)
    for sid, pos in sp.items():
        pts[pos["district"]].append((pos["latitude"], pos["longitude"]))
    centers = {d: (sum(x for x, _ in q) / len(q), sum(y for _, y in q) / len(q)) for d, q in pts.items()}
    return s2d, caps, centers


def load_station_target(profile: str) -> dict[str, int]:
    """該 profile 真實 04:00 快照：逐站目標車量（回收『過量』的基準）。"""
    out: dict[str, int] = {}
    with open(PROJECT_ROOT / f"initial_bikes_4am_{profile}.csv", encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) >= 2 and row[1].strip():
                out[row[0].strip()] = int(round(float(row[1])))
    return out


def load_target_d(profile: str, s2d) -> dict[str, float]:
    """逐區站點總量目標（漂移度量用）。"""
    st = load_station_target(profile)
    td: dict[str, float] = defaultdict(float)
    for sid, b in st.items():
        d = s2d.get(sid)
        if d:
            td[d] += b
    return dict(td)


def load_depot_target(profile: str) -> dict[str, float]:
    """集散場逐區目標（CSV 晨峰淨流量）：補車區(net>0)=net_flow_d、抽車區(net<0)=0。"""
    col = f"net_flow_{profile}"
    out: dict[str, float] = {}
    with open(NETFLOW_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            d = row["sarea"].strip()
            if d in ("Unknown", ""):
                continue
            out[d] = max(0.0, float(row.get(col, 0.0)))
    return out


def route_cost(surplus_d: dict, deficit_d: dict, centers, mother_trucks: int = 0):
    """貪婪最近對配對（組員 Policy 2）：surplus 區 → deficit 區，回傳 (legs, cost)。

    legs: [(from_d, to_d, flow, dist_km)]；intra(from==to)的 dist=0（站→場本地搬，只計裝卸）。
    cost: total/labor/mileage/trip + bikes_moved/km/loads/work_min/fleet。
    """
    S = [[d, v] for d, v in surplus_d.items() if v > 0.5 and d in centers]
    D = [[d, v] for d, v in deficit_d.items() if v > 0.5 and d in centers]
    legs = []
    while sum(v for _, v in S) > 0.5 and sum(v for _, v in D) > 0.5:
        best = None
        for i, (sd, sv) in enumerate(S):
            if sv <= 0.5:
                continue
            for j, (dd, dv) in enumerate(D):
                if dv <= 0.5:
                    continue
                dist = km(centers[sd], centers[dd])
                if best is None or dist < best[0]:
                    best = (dist, i, j)
        if best is None:
            break
        dist, i, j = best
        flow = min(S[i][1], D[j][1])
        legs.append((S[i][0], D[j][0], flow, dist))
        S[i][1] -= flow
        D[j][1] -= flow
    total_loads = 0
    total_km = 0.0
    total_handle = 0.0
    moved = 0.0
    for _, _, flow, dist in legs:
        loads = max(1, math.ceil(flow / MT_CAPACITY))
        total_loads += loads
        total_km += loads * 2 * dist
        total_handle += 2.0 * (loads * HANDLE_FIX + HANDLE_PER_BIKE * flow)
        moved += flow
    drive = total_km / MT_SPEED_KMH * 60.0
    work = total_handle + drive
    required = max(1, math.ceil(work / MT_WINDOW_MIN)) if work > 0 else 0
    fleet = mother_trucks if mother_trucks > 0 else required
    labor = fleet * (MT_WINDOW_MIN / 60.0) * C_LABOR_NIGHT
    cost = {
        "total": round(labor + C_KM * total_km + C_TRIP * total_loads, 1),
        "labor": round(labor, 1), "mileage": round(C_KM * total_km, 1), "trip": round(C_TRIP * total_loads, 1),
        "bikes_moved": round(moved), "km": round(total_km, 1), "loads": total_loads,
        "work_min": round(work), "fleet": fleet,
    }
    return legs, cost


def _recover_from_stations(new_station, station_24h, sids, amount, station_target):
    """從某區站點抽走 amount 台：優先抽『超過 04:00 目標』的過量（按過量比例），
    不夠再按現有車量比例抽。就地更新 new_station。"""
    if amount <= 0 or not sids:
        return
    excess = {s: max(0, station_24h[s] - station_target.get(s, station_24h[s])) for s in sids}
    tot_e = sum(excess.values())
    if tot_e >= amount and tot_e > 0:
        for s in sids:
            new_station[s] = max(0, int(round(new_station[s] - amount * excess[s] / tot_e)))
        return
    # 先抽光所有過量，剩餘按現有比例抽
    for s in sids:
        new_station[s] = max(0, int(round(new_station[s] - excess[s])))
    rem = amount - tot_e
    tot_cur = sum(new_station[s] for s in sids) or 1.0
    for s in sids:
        new_station[s] = max(0, int(round(new_station[s] - rem * new_station[s] / tot_cur)))


def overnight_modelB(mode, station_24h, depot_24h, s2d, station_target, target_d, centers, mother_trucks):
    """Model B 夜間整備（§42.5，修正版）。回傳 (new_station, new_depot, cost|None)。

    目標＝各區站點總量 target_d（04:00 快照逐區總量，已驗證）。只動『失衡』的區：
      盈餘區(total>target)：從過滿站點回收；赤字區(total<target)：補到該區『集散場』(白天 P7 再分到站)。
    量＝實際 delta（非 CSV 毛流量；CSV 只解釋哪些區系統性補/抽）。守恆。
    virtual＝貪婪最近對搬運、計母車成本；full＝免費完美達標。
    """
    districts = set(s2d.values())
    sids_by_d: dict[str, list] = defaultdict(list)
    station_tot: dict[str, float] = defaultdict(float)
    for sid, b in station_24h.items():
        d = s2d.get(sid)
        if d:
            sids_by_d[d].append(sid)
            station_tot[d] += b
    cur_depot = {d: float(depot_24h.get(d, 0.0)) for d in districts}
    total_d = {d: station_tot.get(d, 0.0) + cur_depot.get(d, 0.0) for d in districts}
    delta = {d: target_d.get(d, total_d[d]) - total_d[d] for d in districts}
    surplus = {d: -v for d, v in delta.items() if v < -0.5}   # total>target → 回收
    deficit = {d: v for d, v in delta.items() if v > 0.5}     # total<target → 補到集散場

    new_station = dict(station_24h)
    new_depot = dict(cur_depot)

    if mode == "full":  # 免費完美：盈餘區回收到 target、赤字區集散場補足 delta
        for d, give in surplus.items():
            _recover_from_stations(new_station, station_24h, sids_by_d[d], give, station_target)
        for d, need in deficit.items():
            new_depot[d] = cur_depot.get(d, 0.0) + need
        return new_station, {d: int(round(v)) for d, v in new_depot.items()}, {"total": 0.0}

    # virtual：守恆貪婪——盈餘區回收 → 赤字區集散場
    legs, cost = route_cost(surplus, deficit, centers, mother_trucks)
    rec_by_d: dict[str, float] = defaultdict(float)
    dep_by_d: dict[str, float] = defaultdict(float)
    for sd, dd, flow, _ in legs:
        rec_by_d[sd] += flow
        dep_by_d[dd] += flow
    for d, rec in rec_by_d.items():
        _recover_from_stations(new_station, station_24h, sids_by_d[d], rec, station_target)
    for d, dep in dep_by_d.items():
        new_depot[d] = cur_depot.get(d, 0.0) + dep
    return new_station, {d: int(round(v)) for d, v in new_depot.items()}, cost


def run_day(station_snap: Path, depot_json: Path | None, profile, policy, trucks, subdir, transition_dir, seed):
    """跑一天 → (exc, station_24h{sid:bikes}, depot_24h{district:bikes}, day_cost)。"""
    cmd = [
        sys.executable, str(SCEN), "--hours", str(HOURS), "--profile", profile,
        "--snapshot-csv", str(station_snap), "--start-minute", str(START_MIN),
        "--duty-windows", DUTY, "--seed", str(seed), "--dispatch-policy", policy,
        "--trucks-per-district", str(trucks), "--report-subdir", subdir, "--label", "md",
    ]
    if depot_json:
        cmd += ["--depot-init-json", str(depot_json)]
    if transition_dir:
        cmd += ["--transition-dir", transition_dir]
    res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    dirs = sorted(glob.glob(str(REPORT / subdir / "*")), key=os.path.getmtime)
    sjson = None
    for d in reversed(dirs):
        if os.path.exists(os.path.join(d, "summary.json")):
            sjson = os.path.join(d, "summary.json")
            break
    if not sjson:
        sys.exit(f"day run 無 summary：{res.stdout[-500:]}\n{res.stderr[-800:]}")
    s = json.load(open(sjson, encoding="utf-8"))
    exc = s["metrics"]["excellent"]["mean"]
    disp = s.get("dispatch", {}) or {}
    day_cost = float(disp.get("cost_breakdown", {}).get("total", 0.0) or s.get("sc_ratio_block", {}).get("dispatching_cost", 0.0))
    station_24h = {str(x["station_id"]): int(x["available_bikes"]) for x in s["station_snapshots"]}
    depot_24h = {str(d): int(v) for d, v in (disp.get("depot_inventory_end", {}) or {}).items()}
    return exc, station_24h, depot_24h, day_cost


def write_station_snapshot(bikes: dict, path: Path):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sno", "bikes"])
        for sid, b in bikes.items():
            w.writerow([sid, b])


def write_depot_json(depot: dict, path: Path):
    path.write_text(json.dumps({str(d): int(v) for d, v in depot.items()}, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="weekday")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--week-mode", choices=["single", "realweek"], default="realweek")
    ap.add_argument("--overnight", choices=["none", "virtual", "full", "full_station"], default="virtual")
    ap.add_argument("--policy", default="pair_coord")
    ap.add_argument("--trucks", type=int, default=8)
    ap.add_argument("--transition-dir", default="")
    ap.add_argument("--seed-mode", choices=["fixed", "perday"], default="perday")
    ap.add_argument("--mother-trucks", type=int, default=0, help="virtual 母車上限；0=自動。")
    args = ap.parse_args()

    s2d, caps, centers = load_static()
    profiles = {day_profile(d, args.week_mode, args.profile) for d in range(1, args.days + 2)}
    target_d = {p: load_target_d(p, s2d) for p in profiles}
    station_target = {p: load_station_target(p) for p in profiles}
    depot_target = {p: load_depot_target(p) for p in profiles}
    base_snap = {p: PROJECT_ROOT / f"initial_bikes_4am_{p}.csv" for p in profiles}

    def trans_for(profile):
        if args.week_mode == "single":
            return args.transition_dir
        return SMOOTHED_OD if profile == "weekend" else ""

    label = "realweek" if args.week_mode == "realweek" else args.profile
    tag = f"exp10b_multiday_{label}_{args.overnight}"
    tmp_snap = REPORT / f"_mdb_snap_{label}_{args.overnight}.csv"
    tmp_depot = REPORT / f"_mdb_depot_{label}_{args.overnight}.json"
    cur_snap = base_snap[day_profile(1, args.week_mode, args.profile)]
    cur_depot_json = None  # Day1 用守恆預設集散場

    cal = "".join("WE" if day_profile(d, args.week_mode, args.profile) == "weekend" else "wd "
                  for d in range(1, args.days + 1))
    print(f"=== 多日(ModelB) {label} | {args.days}天 | overnight={args.overnight} | {args.policy}@{args.trucks} | seed={args.seed_mode} ===")
    if args.week_mode == "realweek":
        print(f"    週曆：{cal}")
    daily = []
    night_total = day_cost_total = 0.0
    for day in range(1, args.days + 1):
        prof = day_profile(day, args.week_mode, args.profile)
        next_prof = day_profile(day + 1, args.week_mode, args.profile) if day < args.days else prof
        seed = SEED_BASE + day if args.seed_mode == "perday" else SEED_BASE
        exc, station_24h, depot_24h, day_cost = run_day(
            cur_snap, cur_depot_json, prof, args.policy, args.trucks,
            f"{tag}/day{day:02d}", trans_for(prof), seed)
        day_cost_total += day_cost
        cur_dd = defaultdict(float)
        for sid, b in station_24h.items():
            d = s2d.get(sid)
            if d:
                cur_dd[d] += b
        drift = sum(abs(cur_dd.get(d, 0) - target_d[prof][d]) for d in target_d[prof])
        depot_end = sum(depot_24h.values())

        night = ""
        nc = 0.0
        if args.overnight == "full_station":
            cur_snap = base_snap[next_prof]      # 站點完美還原
            cur_depot_json = None                # 集散場守恆預設
        elif args.overnight == "none":
            write_station_snapshot(station_24h, tmp_snap)
            write_depot_json(depot_24h, tmp_depot)
            cur_snap, cur_depot_json = tmp_snap, tmp_depot
        else:  # virtual / full → Model B 整備到集散場（目標朝隔天 profile）
            ns, nd, cost = overnight_modelB(args.overnight, station_24h, depot_24h, s2d,
                                            station_target[next_prof], target_d[next_prof], centers, args.mother_trucks)
            write_station_snapshot(ns, tmp_snap)
            write_depot_json(nd, tmp_depot)
            cur_snap, cur_depot_json = tmp_snap, tmp_depot
            nc = cost["total"] if args.overnight == "virtual" else 0.0
            night_total += nc
            if args.overnight == "virtual":
                night = (f"  母車{cost['fleet']}台/回收送場{cost['bikes_moved']}台/{cost['km']:.0f}km/夜NT${nc:,.0f}"
                         f"｜集散場補到{sum(nd.values())}台")
        daily.append((day, prof, exc, drift, nc))
        ptag = "WE" if prof == "weekend" else "wd"
        print(f"  Day{day:02d}[{ptag}] 優良={exc:.3f} 漂移={drift:.0f} 集散場24h={depot_end}{night}")

    print("\n每日優良：", " ".join(f"{e:.3f}" for _, _, e, _, _ in daily))
    wd = [e for _, p, e, _, _ in daily if p == "weekday"]
    we = [e for _, p, e, _, _ in daily if p == "weekend"]
    if wd:
        print(f"  週間({len(wd)}天) 均 {sum(wd)/len(wd):.3f}" + (f"　週末({len(we)}天) 均 {sum(we)/len(we):.3f}" if we else ""))
    e1, eN = daily[0][2], daily[-1][2]
    print(f"Day1={e1:.3f} → Day{args.days}={eN:.3f}  Δ={eN-e1:+.3f}")
    if args.overnight == "virtual" and night_total > 0:
        share = night_total / day_cost_total * 100 if day_cost_total > 0 else 0
        print(f"夜間母車總成本 NT${night_total:,.0f}（白天總調度 NT${day_cost_total:,.0f} 的 {share:.1f}%）")


if __name__ == "__main__":
    main()
