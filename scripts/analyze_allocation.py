"""per-district 逐區配車分析（§39）。

利用「行政區獨立」（區內調度、卡車不跨區）：從 P7 **uniform-N** 運行抽出各區的
excellent(N) 與 cost(N) 曲線，據此**解析計算**任意總預算 T 下的：
  - uniform：每區 T/13（需整除）。
  - stations / demand：∝站數 / ∝需求 的結構式配置。
  - optimal：貪婪邊際最優（每台車加到「站數×Δexcellent 最大」的區；excellent(N) 凹 → 貪婪最優）。
並印出各策略的「預測全市優良」（站數加權平均），以及把 optimal 配置輸出成 JSON 供實跑驗證。

注意：跨區仍有微弱耦合（騎乘流、缺車影響流量），故預測值為近似；實跑值以 joint sim 為準。

用法：python scripts/analyze_allocation.py --profile weekday --totals 78 104 156 [--emit-optimal]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT = PROJECT_ROOT / "report"


def load_uniform_curves(profile: str):
    """從 P7 uniform-N 運行抽 exc[d][N]、cost[d][N]、nst[d]、overall[N]。"""
    exc: dict[str, dict[int, float]] = {}
    cost: dict[str, dict[int, float]] = {}
    nst: dict[str, int] = {}
    overall: dict[int, float] = {}
    dirs = glob.glob(str(REPORT / f"exp06_real_snapshot_{profile}/*")) + \
        glob.glob(str(REPORT / f"exp07_truck_sweep_{profile}/*"))
    for d in sorted(dirs):
        sp = os.path.join(d, "summary.json")
        if not os.path.exists(sp):
            continue
        s = json.load(open(sp, encoding="utf-8"))
        disp = s.get("dispatch", {})
        if disp.get("policy") != "pair_coord" or disp.get("truck_allocation"):
            continue  # 只要 uniform P7
        N = int(disp.get("trucks_per_district", 0))
        if N <= 0:
            continue
        db = s.get("metrics", {}).get("excellent", {}).get("district_breakdown", {}) or {}
        dc = disp.get("district_cost", {}) or {}
        overall[N] = s.get("metrics", {}).get("excellent", {}).get("mean")
        for dist, info in db.items():
            exc.setdefault(dist, {})[N] = info.get("excellent_mean")
            nst[dist] = info.get("n_stations", nst.get(dist, 0))
            cost.setdefault(dist, {})[N] = (dc.get(dist, {}) or {}).get("total", 0.0)
    return exc, cost, nst, overall


def predict(alloc: dict[str, int], exc, cost, nst) -> tuple[float, float]:
    """站數加權預測全市優良 + 總成本（用最接近的已測 N 補洞）。"""
    def at(curve, d, n):
        if n in curve[d]:
            return curve[d][n]
        ks = sorted(curve[d])
        return curve[d][min(ks, key=lambda k: abs(k - n))]  # 最近鄰補洞
    tot_n = sum(nst[d] for d in alloc)
    wexc = sum(nst[d] * at(exc, d, alloc[d]) for d in alloc) / tot_n
    tcost = sum(at(cost, d, alloc[d]) for d in alloc)
    return wexc, tcost


def alloc_structural(weight: dict[str, float], total: int, min_per: int = 1) -> dict[str, int]:
    ds = sorted(weight)
    a = {d: min_per for d in ds}
    rem = total - min_per * len(ds)
    tw = sum(weight.values()) or 1.0
    raw = {d: rem * weight[d] / tw for d in ds}
    fl = {d: int(raw[d]) for d in ds}
    for d in ds:
        a[d] += fl[d]
    for d in sorted(ds, key=lambda x: raw[x] - fl[x], reverse=True)[:rem - sum(fl.values())]:
        a[d] += 1
    return a


def alloc_greedy_optimal(total: int, exc, nst, min_per: int = 1) -> dict[str, int]:
    """貪婪：每台車加到『站數×Δexcellent』最大的區。excellent(N) 凹 → 貪婪最優。"""
    ds = sorted(exc)
    a = {d: min_per for d in ds}
    maxN = {d: max(exc[d]) for d in ds}

    def gain(d):
        n = a[d]
        if n + 1 not in exc[d] and n + 1 > maxN[d]:
            return -1.0  # 已達曲線上限
        nxt = exc[d].get(n + 1)
        cur = exc[d].get(n)
        if nxt is None or cur is None:
            ks = sorted(exc[d])
            nxt = exc[d][min(ks, key=lambda k: abs(k - (n + 1)))]
            cur = exc[d][min(ks, key=lambda k: abs(k - n))]
        return nst[d] * max(0.0, nxt - cur)

    for _ in range(total - min_per * len(ds)):
        best = max(ds, key=gain)
        a[best] += 1
    return a


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="weekday")
    ap.add_argument("--totals", type=int, nargs="+", default=[78, 104, 156])
    ap.add_argument("--emit-optimal", action="store_true", help="輸出 optimal 配置 JSON（供實跑）")
    args = ap.parse_args()

    exc, cost, nst, overall = load_uniform_curves(args.profile)
    if not exc:
        sys.exit("找不到 P7 uniform 曲線")
    stations_w = {d: float(nst[d]) for d in nst}
    # 需求權重：用 cost 無關，改用站數×平均 excellent? 不，需求要另抽。這裡用站數近似 demand 不可，
    # 但 demand 配置實跑已直接用 λ；分析預測用 stations 與 optimal 即可（demand 預測略）。
    print(f"已測 uniform N：{sorted(overall)}")
    print(f"行政區站數：{ {d: nst[d] for d in sorted(nst, key=lambda x:-nst[x])} }\n")
    emit = {}
    for T in args.totals:
        print(f"===== 總車隊 T={T}（≈uniform {T/13:.1f}/區）=====")
        rows = []
        if T % 13 == 0 and (T // 13) in overall:
            uN = T // 13
            rows.append(("uniform", {d: uN for d in nst}, overall[uN]))
        rows.append(("stations", alloc_structural(stations_w, T), None))
        opt = alloc_greedy_optimal(T, exc, nst)
        rows.append(("optimal", opt, None))
        for name, a, actual in rows:
            pe, pc = predict(a, exc, cost, nst)
            note = f"（實測全市 {actual:.3f}）" if actual is not None else ""
            print(f"  {name:<9} 預測優良 {pe:.3f} 成本 {pc/1000:>6.0f}k {note}")
        worst = min(opt, key=lambda d: exc[d].get(opt[d], min(exc[d].values())))
        print(f"  optimal 配置：{ {d: opt[d] for d in sorted(opt, key=lambda x:-opt[x])} }")
        emit[str(T)] = opt
        print()
    if args.emit_optimal:
        out = REPORT / f"_optimal_alloc_{args.profile}.json"
        out.write_text(json.dumps(emit, ensure_ascii=False), encoding="utf-8")
        print(f"optimal 配置 JSON 已存：{out}")


if __name__ == "__main__":
    main()
