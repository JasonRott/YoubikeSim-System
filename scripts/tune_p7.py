"""P7 長時間調參（§43.4）。

每組跑「1 週連續」（overnight=virtual，部署系統含區間調度），用 **day5–7 穩態均** 評分（非 day1 理想）。
兩階段網格避免組合爆炸又保留交互作用：
  Stage1：target_low/high × alert_low/high（4×3=12，固定 min_action=0.15）→ 挑最佳帶。
  Stage2：min_action {0.10/0.15/0.20}（3，用 Stage1 最佳帶）→ 微調 → 定案最佳行為參數。
  Sweep：最佳行為參數 × 車數 {4,6,8,10,12}（5）→ 初步 SC/EC Pareto（最終再升級 30天+多seed）。
共 20 組 × 7天。選擇主指標＝day5-7 穩態 PoE（優良占比，專案品質標準）；同時報 SL/成本/SC。

用法：python scripts/tune_p7.py   （背景跑；結果印到 stdout，逐組落地 report/exp13_tune_*）
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT / "scenarios" / "real_system_scenario.py"
REPORT = ROOT / "report"
DAYS, SEED, PROFILE = 7, 20260603, "weekday"
SNAP = "data/snapshots/initial_bikes_4am_weekday.csv"


def run_cfg(cfg: dict, trucks: int, subdir: str, label: str, alloc: str = "uniform"):
    """跑一組 7 天連續，回傳 (poe5_7, sl5_7, day_cost, night_cost, sc)。

    alloc="uniform"：每區 trucks 台（固定配置，行為參數調參用，與配置無關）。
    alloc="demand" ：trucks 為**總車隊**、按各區需求 λ 分配（§39 證明省車隊；最終 Pareto 橫軸用此）。
    """
    ov = ",".join(f"{k}={v}" for k, v in cfg.items())
    cmd = [
        sys.executable, str(SCEN), "--hours", "20", "--profile", PROFILE,
        "--snapshot-csv", SNAP, "--start-minute", "240", "--duty-windows", "240-1440",
        "--seed", str(SEED), "--dispatch-policy", "pair_coord",
        "--days", str(DAYS), "--overnight-mode", "virtual",
        "--report-subdir", subdir, "--label", label,
    ]
    if alloc == "demand":
        cmd += ["--truck-allocation", "demand", "--total-trucks", str(trucks)]
    else:
        cmd += ["--trucks-per-district", str(trucks)]
    if ov:
        cmd += ["--dispatch-config-override", ov]
    subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    dirs = sorted(glob.glob(str(REPORT / subdir / "*")), key=os.path.getmtime)
    for d in reversed(dirs):
        p = os.path.join(d, "summary.json")
        if os.path.exists(p):
            s = json.load(open(p, encoding="utf-8"))
            md = s["multiday"]
            pde, pdsl = md["per_day_excellent"], md["per_day_service_level"]
            poe = sum(pde[4:7]) / 3.0
            sls = [x for x in pdsl[4:7] if x is not None]
            sl = sum(sls) / len(sls) if sls else None
            day_cost = float((s.get("dispatch", {}) or {}).get("cost_breakdown", {}).get("total", 0.0))
            night = float(md.get("night_cost_total", 0.0))
            sc = (sl / ((day_cost + night) / DAYS) * 1e6) if (sl and (day_cost + night) > 0) else None
            return poe, sl, day_cost, night, sc
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    print("=== P7 調參（1週連續/overnight=virtual/day5-7穩態評分）===\n")
    # ---- Stage 1: target × alert（固定 min_action=0.15, trucks=8）----
    targets = [(0.25, 0.75), (0.30, 0.70), (0.35, 0.65), (0.40, 0.60)]
    alerts = [(0.10, 0.90), (0.15, 0.85), (0.20, 0.80)]
    print("### Stage1: target × alert (min_action=0.15, trucks=8) ###")
    s1 = []
    for tl, th in targets:
        for al, ah in alerts:
            cfg = {"target_low_ratio": tl, "target_high_ratio": th,
                   "alert_low_ratio": al, "alert_high_ratio": ah, "min_action_ratio": 0.15}
            r = run_cfg(cfg, 8, "exp13_tune_s1", f"t{tl}-{th}_a{al}-{ah}")
            s1.append((cfg, r))
            if r:
                print(f"  target {tl}/{th} alert {al}/{ah}:  PoE5-7={r[0]:.4f}  SL={r[1]:.4f}  "
                      f"白天成本={r[2]:,.0f} 夜={r[3]:,.0f}  SC={r[4]:.4f}")
            else:
                print(f"  target {tl}/{th} alert {al}/{ah}:  FAIL")
    s1ok = [(c, r) for c, r in s1 if r]
    best1 = max(s1ok, key=lambda x: x[1][0])  # 選 day5-7 PoE 最高
    bc = best1[0]
    print(f"\n  → Stage1 最佳(依PoE): target {bc['target_low_ratio']}/{bc['target_high_ratio']} "
          f"alert {bc['alert_low_ratio']}/{bc['alert_high_ratio']}  PoE={best1[1][0]:.4f} SC={best1[1][4]:.4f}\n")

    # ---- Stage 2: min_action（用 Stage1 最佳帶, trucks=8）----
    print("### Stage2: min_action (用 Stage1 最佳帶, trucks=8) ###")
    s2 = []
    for ma in (0.10, 0.15, 0.20):
        cfg = dict(bc)
        cfg["min_action_ratio"] = ma
        r = run_cfg(cfg, 8, "exp13_tune_s2", f"ma{ma}")
        s2.append((cfg, r))
        if r:
            print(f"  min_action {ma}:  PoE5-7={r[0]:.4f}  SL={r[1]:.4f}  SC={r[4]:.4f}")
    s2ok = [(c, r) for c, r in s2 if r]
    best2 = max(s2ok, key=lambda x: x[1][0])
    best_cfg = best2[0]
    print(f"\n  → 定案最佳行為參數: {best_cfg}  PoE={best2[1][0]:.4f} SC={best2[1][4]:.4f}\n")

    # ---- Sweep: 車數（用定案最佳行為參數, ∝需求配置）→ 初步 Pareto ----
    # ∝需求（非均勻）：§39 證明均勻配置沒效率；最終 Pareto 橫軸用總車隊+依需求分配。
    print("### 車數 sweep (定案行為參數, ∝需求配置) → 初步 SC/EC Pareto ###")
    print("總車隊 | PoE5-7 | SL5-7 | 白天成本 | 夜成本 | SC")
    for tot in (52, 78, 104, 130, 156):  # ≈ 均勻 4/6/8/10/12 的等量車隊，但依需求分配
        r = run_cfg(best_cfg, tot, "exp13_tune_sweep", f"total{tot}", alloc="demand")
        if r:
            print(f"  {tot:4d}  | {r[0]:.4f} | {r[1]:.4f} | {r[2]:,.0f} | {r[3]:,.0f} | {r[4]:.4f}")
    print("\n定案最佳行為參數:", best_cfg)
    print("（最終 SC/EC Pareto 用此參數 + ∝需求配置 + 多 seed 升級。）")


if __name__ == "__main__":
    main()
