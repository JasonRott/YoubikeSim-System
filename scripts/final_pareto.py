"""最終 SC/EC Pareto 網格（§43 收尾）。

三維：target 帶 × ∝需求車隊 × 多 seed。每點 7 天連續、overnight=virtual、取 day5-7 穩態。
丟掉 alert/min_action（P7 不響應、no-op）。結果逐次寫入 report/_final_pareto.csv（可中斷續跑）。
繪圖另做（從 CSV）。

在「你自己的 PowerShell」跑：powershell -ExecutionPolicy Bypass -File scripts\run_pareto.ps1
"""

from __future__ import annotations

import csv
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCEN = ROOT / "scenarios" / "real_system_scenario.py"
REPORT = ROOT / "report"
OUT_CSV = REPORT / "_final_pareto.csv"

DAYS, PROFILE, SNAP = 7, "weekday", "data/snapshots/initial_bikes_4am_weekday.csv"
# ---- 網格（要調規模就改這三行）----
BANDS = [(0.40, 0.60), (0.35, 0.65), (0.30, 0.70), (0.25, 0.75)]   # target_low/high
TRUCKS = [52, 78, 104, 130, 156]                                   # 總車隊（∝需求分配）
SEEDS = [20260603, 20260604, 20260605]                            # 多 seed


def run_one(tl, th, tot, seed, subdir):
    ov = f"target_low_ratio={tl},target_high_ratio={th}"
    cmd = [
        sys.executable, str(SCEN), "--hours", "20", "--profile", PROFILE, "--snapshot-csv", SNAP,
        "--start-minute", "240", "--duty-windows", "240-1440", "--seed", str(seed),
        "--dispatch-policy", "pair_coord", "--truck-allocation", "demand", "--total-trucks", str(tot),
        "--days", str(DAYS), "--overnight-mode", "virtual",
        "--report-subdir", subdir, "--label", "pareto", "--dispatch-config-override", ov,
    ]
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
            dc = float((s.get("dispatch", {}) or {}).get("cost_breakdown", {}).get("total", 0.0))
            night = float(md.get("night_cost_total", 0.0))
            sc = sl / ((dc + night) / DAYS) * 1e6 if (sl and dc + night > 0) else None
            return poe, sl, dc, night, sc
    return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    # 續跑：已存在的 (band,trucks,seed) 跳過。
    done = set()
    if OUT_CSV.exists():
        for r in csv.DictReader(open(OUT_CSV, encoding="utf-8")):
            done.add((r["band"], r["total_trucks"], r["seed"]))
    else:
        with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(["band", "total_trucks", "seed", "poe5_7", "sl5_7", "day_cost", "night_cost", "sc"])

    n = len(BANDS) * len(TRUCKS) * len(SEEDS)
    i = 0
    for tl, th in BANDS:
        band = f"{tl}/{th}"
        for tot in TRUCKS:
            for seed in SEEDS:
                i += 1
                key = (band, str(tot), str(seed))
                if key in done:
                    print(f"[{i}/{n}] skip (done) band {band} trucks {tot} seed {seed}", flush=True)
                    continue
                sub = f"exp15_pareto/b{tl}-{th}_t{tot}_s{seed}"
                r = run_one(tl, th, tot, seed, sub)
                if r:
                    print(f"[{i}/{n}] band {band} trucks {tot} seed {seed}:  PoE={r[0]:.4f} SL={r[1]:.4f} SC={r[4]:.4f}", flush=True)
                    with open(OUT_CSV, "a", encoding="utf-8", newline="") as f:
                        csv.writer(f).writerow([band, tot, seed, round(r[0], 4), round(r[1], 4), round(r[2], 1), round(r[3], 1), round(r[4], 4)])
                else:
                    print(f"[{i}/{n}] band {band} trucks {tot} seed {seed}:  FAIL", flush=True)
    print(f"\nDONE. CSV: {OUT_CSV}")


if __name__ == "__main__":
    main()
