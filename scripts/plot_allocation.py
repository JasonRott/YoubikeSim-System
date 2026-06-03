"""逐區配車對照圖（§39）：uniform / 依站數 / ∝需求 / 貪婪最優 四種配置，
在「總調度成本 × 優良占比」上各畫一條線，看同成本下哪種配置服務最高（配置效率）。

uniform 取自 P7 均勻車數掃描（total = N×13）；其餘取自 exp09 的 variant_label（stat_/dem_/opt_）。
用法：python scripts/plot_allocation.py --profile weekday
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

import csv
import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT = PROJECT_ROOT / "report"
BENCHMARK_CSV = PROJECT_ROOT / "data/benchmark/percentage_of_excellent/站點優良時段比例.csv"


def benchmark_mean(profile: str) -> float:
    """真實 benchmark 均值，依 profile 過濾週間/週末（§41）。weekday≈0.598、weekend≈0.632。"""
    if not BENCHMARK_CSV.exists():
        return 0.607
    want_weekend = {"weekend": True, "weekday": False}.get(profile)
    vals = []
    with BENCHMARK_CSV.open(encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 3 or not row[2]:
                continue
            if want_weekend is not None:
                try:
                    if (datetime.datetime.fromisoformat(row[1].strip()).weekday() >= 5) != want_weekend:
                        continue
                except (ValueError, IndexError):
                    continue
            try:
                vals.append(float(row[2]))
            except ValueError:
                continue
    return round(sum(vals) / len(vals), 4) if vals else 0.607


def load(profile: str):
    series = {"uniform": [], "stations": [], "demand": [], "optimal": []}
    # uniform：P7 均勻
    for sub in (f"exp06_real_snapshot_{profile}", f"exp07_truck_sweep_{profile}"):
        for d in sorted(glob.glob(str(REPORT / sub / "*"))):
            sp = os.path.join(d, "summary.json")
            if not os.path.exists(sp):
                continue
            s = json.load(open(sp, encoding="utf-8"))
            disp = s.get("dispatch", {})
            if disp.get("policy") != "pair_coord" or disp.get("truck_allocation"):
                continue
            N = int(disp.get("trucks_per_district", 0))
            if N <= 0:
                continue
            series["uniform"].append(_pt(s, N * 13))
    # 配置策略：exp09 variant_label 前綴
    pref = {"stat_": "stations", "dem_": "demand", "opt_": "optimal"}
    for d in sorted(glob.glob(str(REPORT / f"exp09_allocation_{profile}/*"))):
        sp = os.path.join(d, "summary.json")
        if not os.path.exists(sp):
            continue
        s = json.load(open(sp, encoding="utf-8"))
        v = s.get("dispatch", {}).get("variant_label", "")
        for pfx, name in pref.items():
            if v.startswith(pfx):
                series[name].append(_pt(s, s["dispatch"].get("trucks_per_district")))
    for k in series:
        series[k].sort(key=lambda p: p["total"])
    return series


def _pt(s, total):
    scb = s.get("sc_ratio_block", {})
    return {
        "total": total,
        "cost": float(scb.get("dispatching_cost") or 0.0),
        "excellent": float(s["metrics"]["excellent"]["mean"]),
        "sl": float(scb.get("service_level") or 0.0),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="weekday")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    series = load(args.profile)

    print(f"配置對照（{args.profile}）— 同總車隊下優良占比：")
    print(f"{'總車':>5}{'uniform':>9}{'stations':>9}{'demand':>9}{'optimal':>9}")
    totals = sorted({p["total"] for s in series.values() for p in s})
    idx = {k: {p["total"]: p for p in series[k]} for k in series}
    for t in totals:
        row = f"{t:>5}"
        for k in ("uniform", "stations", "demand", "optimal"):
            p = idx[k].get(t)
            row += f"{p['excellent']:>9.3f}" if p else f"{'-':>9}"
        print(row)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception as e:
        print(f"(無 matplotlib：{e})")
        return
    style = {"uniform": ("#888", "o", "均勻配置"), "stations": ("#2f78b5", "s", "依站數"),
             "demand": ("#d98a3d", "^", "依需求λ"), "optimal": ("#c42f2f", "*", "貪婪最優(上界)")}
    fig, ax = plt.subplots(figsize=(9, 6))
    for k, (c, m, lab) in style.items():
        pts = series[k]
        if not pts:
            continue
        ax.plot([p["cost"] / 1000 for p in pts], [p["excellent"] for p in pts],
                marker=m, color=c, label=lab, markersize=9 if m == "*" else 6, alpha=0.9)
    ts = benchmark_mean(args.profile)
    ax.axhline(ts, color="#4d8f5f", ls=":", label=f"真實參考 {ts}")
    ax.set_xlabel("總調度成本（千 NT$/日）")
    ax.set_ylabel("優良時段占比")
    ax.set_title(f"逐區配車效率對照 — {args.profile}（4am）")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    plt.tight_layout()
    out = Path(args.out) if args.out else (REPORT / f"allocation_{args.profile}.png")
    plt.savefig(out, dpi=130)
    plt.close()
    print(f"\n圖已存：{out}")


if __name__ == "__main__":
    main()
