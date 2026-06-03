"""最終 SC/EC Pareto 繪圖（§43 收尾）。

讀 report/_final_pareto.csv（target帶 × ∝需求車隊 × 多seed），畫：
  (A) 品質-成本前緣：PoE(day5-7) vs 每日成本，按 target 帶上色、5 車隊點、seed 平均±std、
      標 0.60 品質線與膝點工作點。
  (B) 品質↔效率撥盤：固定膝點車隊(104)下，4 個 target 帶的 PoE vs SC（成反比）。
輸出 report/_final_pareto.png。
"""

from __future__ import annotations

import csv
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "report" / "_final_pareto.csv"
OUT = ROOT / "report" / "_final_pareto.png"
DAYS = 7
KNEE_TRUCKS = 104
BAND_ORDER = ["0.4/0.6", "0.35/0.65", "0.3/0.7", "0.25/0.75"]
BAND_COLOR = {"0.4/0.6": "#1f77b4", "0.35/0.65": "#2ca02c",
              "0.3/0.7": "#ff7f0e", "0.25/0.75": "#d62728"}


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        key = (r["band"], int(r["total_trucks"]))
        daily = (float(r["day_cost"]) + float(r["night_cost"])) / DAYS
        agg[key]["poe"].append(float(r["poe5_7"]))
        agg[key]["sc"].append(float(r["sc"]))
        agg[key]["cost"].append(daily)

    def mean(key, f):
        return st.mean(agg[key][f])

    def std(key, f):
        return st.pstdev(agg[key][f])

    trucks = sorted({int(r["total_trucks"]) for r in rows})
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(14, 5.6))

    # ---- (A) 品質-成本前緣 ----
    for band in BAND_ORDER:
        xs = [mean((band, t), "cost") / 1000 for t in trucks]
        ys = [mean((band, t), "poe") for t in trucks]
        es = [std((band, t), "poe") for t in trucks]
        axA.errorbar(xs, ys, yerr=es, marker="o", ms=5, capsize=3,
                     color=BAND_COLOR[band], label=f"band {band}")
        for t, x, y in zip(trucks, xs, ys):
            axA.annotate(f"{t}", (x, y), textcoords="offset points",
                         xytext=(4, -10), fontsize=7, color=BAND_COLOR[band])
    axA.axhline(0.60, ls="--", color="gray", alpha=0.7)
    axA.text(axA.get_xlim()[1], 0.601, " PoE=0.60 quality bar", fontsize=8,
             va="bottom", ha="right", color="gray")
    # 標膝點工作點（0.4/0.6 @104）
    kx = mean(("0.4/0.6", KNEE_TRUCKS), "cost") / 1000
    ky = mean(("0.4/0.6", KNEE_TRUCKS), "poe")
    axA.scatter([kx], [ky], s=200, facecolors="none", edgecolors="black",
                linewidths=2, zorder=5)
    axA.annotate(f"working point\nband 0.40/0.60, {KNEE_TRUCKS} trucks\nPoE={ky:.3f}",
                 (kx, ky), textcoords="offset points", xytext=(10, 12), fontsize=8)
    axA.set_xlabel("daily dispatching cost (NT$ thousand / day)")
    axA.set_ylabel("PoE (excellent-ratio, day5-7 steady)")
    axA.set_title("(A) Quality-Cost frontier  (label = total fleet, demand-allocated)")
    axA.legend(fontsize=8)
    axA.grid(alpha=0.3)

    # ---- (B) 品質↔效率撥盤（固定膝點車隊）----
    bx = [mean((b, KNEE_TRUCKS), "sc") for b in BAND_ORDER]
    by = [mean((b, KNEE_TRUCKS), "poe") for b in BAND_ORDER]
    for b, x, y in zip(BAND_ORDER, bx, by):
        axB.scatter([x], [y], s=90, color=BAND_COLOR[b], zorder=5)
        axB.annotate(f"band {b}", (x, y), textcoords="offset points",
                     xytext=(6, 0), fontsize=8, va="center")
    axB.plot(bx, by, color="gray", alpha=0.5, lw=1)
    axB.set_xlabel("SC = ServiceLevel / cost  (x1e6, higher = more efficient)")
    axB.set_ylabel("PoE (excellent-ratio, day5-7)")
    axB.set_title(f"(B) Quality<->Efficiency dial @ {KNEE_TRUCKS} trucks  (band is INVERSE)")
    axB.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUT, dpi=130)
    print("saved", OUT)
    print(f"\nWorking point: band 0.40/0.60, {KNEE_TRUCKS} trucks (demand-allocated)")
    print(f"  PoE={ky:.4f}  SC={mean(('0.4/0.6',KNEE_TRUCKS),'sc'):.4f}  "
          f"cost/day={kx:.0f}k")


if __name__ == "__main__":
    main()
