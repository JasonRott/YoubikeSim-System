"""SC / EC 效率前緣圖（專案核心產出，見 architecture_narrative §36②/§37）。

讀某 profile 的實驗報告 summary.json，輸出**兩個獨立檔案**：
  *_sc.png：成本 × ServiceLevel —— SC = SL/成本（原點斜率）。只採納 SL≥sl_accept(預設0.8)：
            前緣與最高斜率線都只連 SL≥0.8 的點；y 軸從 sc_ymin(預設0.6) 起。
  *_ec.png：成本 × 優良占比 —— 效率前緣 + 最高斜率線。none＝基準起點(cost0, 優良0.289)，
            最高斜率線從 none 出發、只切優良≥ex_accept(預設0.55) 的點；前緣同樣只連≥0.55。
            另標真實參考 0.607、品質門檻、實測 UB 天花板線。

可重用於 (a) 策略間對比、(b) P7 參數敏感度（--dirs 指到 P7 grid 報告）。
用法：python scripts/plot_sc_pareto.py --profile weekday [--sl-accept 0.8] [--ex-accept 0.55]
      [--dirs <子資料夾...>] [--out-prefix report/sc_pareto_weekday] [--title "策略間對比"]
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
    """真實「優良時段占比」benchmark 均值，依 profile 過濾週間/週末（見 §41）。
    weekday≈0.598、weekend≈0.632、合併≈0.607。檔不存在時退回 0.607。"""
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
POLICY_LABEL = {
    "none": "none", "fixed": "P1", "dynamic": "P2", "hybrid_anticipatory": "P3",
    "hybrid_smartshift": "P4", "hybrid_forecast": "P6", "pair_coord": "P7", "optimal_ub": "UB",
}


def collect(dirs: list[str]) -> list[dict]:
    pts: list[dict] = []
    for sub in dirs:
        for d in sorted(glob.glob(str(REPORT / sub / "*"))):
            sp = os.path.join(d, "summary.json")
            if not os.path.exists(sp):
                continue
            s = json.load(open(sp, encoding="utf-8"))
            disp = s.get("dispatch", {}) or {}
            scb = s.get("sc_ratio_block", {}) or {}
            ex = (s.get("metrics", {}) or {}).get("excellent", {}) or {}
            pol = disp.get("policy", "none")
            pts.append({
                "policy": pol, "label": POLICY_LABEL.get(pol, pol),
                "trucks": int(disp.get("trucks_per_district", 0) or 0),
                "cost": float(scb.get("dispatching_cost") or 0.0),
                "sl": float(scb.get("service_level") or 0.0),
                "excellent": float(ex.get("mean") or 0.0),
                # 任意標籤鍵（P7 grid 用 config 區分；策略間用 policy）
                "variant": disp.get("variant_label"),
            })
    return pts


def pareto_front(pts: list[dict], yk: str) -> list[dict]:
    """非被支配點（成本更低且 y 不低 → 支配）。輸入應已先過濾掉不採納的點。"""
    front = []
    for p in pts:
        if any((q["cost"] <= p["cost"] and q[yk] >= p[yk]) and (q["cost"] < p["cost"] or q[yk] > p[yk])
               for q in pts if q is not p):
            continue
        front.append(p)
    return sorted(front, key=lambda x: x["cost"])


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


COLORS = {"P1": "#888", "P4": "#aaa", "P2": "#2f78b5", "P3": "#7f5fa0",
          "P6": "#d98a3d", "P7": "#c42f2f", "none": "#444", "UB": "#4d8f5f"}


def _scatter(ax, pts, yk, dim_below=None):
    for p in pts:
        c = COLORS.get(p["label"], "#333")
        faded = (dim_below is not None and p[yk] < dim_below)
        ax.scatter(p["cost"] / 1000, p[yk], color=c, s=55,
                   alpha=0.25 if faded else 1.0, zorder=3,
                   edgecolors="none" if faded else "white", linewidths=0.5)
        tag = p["variant"] or f"{p['label']}{p['trucks'] or ''}"
        ax.annotate(tag, (p["cost"] / 1000, p[yk]), fontsize=7,
                    alpha=0.4 if faded else 0.9, xytext=(4, 3), textcoords="offset points")


def make_sc_chart(pts, out, sl_accept, ymin, title):
    plt = _plt()
    feas = [p for p in pts if p["policy"] != "optimal_ub"]
    priced = [p for p in feas if p["cost"] > 0]
    accept = [p for p in priced if p["sl"] >= sl_accept]
    fig, ax = plt.subplots(figsize=(8, 6))
    _scatter(ax, feas, "sl", dim_below=sl_accept)
    # 前緣：只連 SL≥sl_accept 的點。
    front = pareto_front(accept, "sl")
    if front:
        ax.plot([p["cost"] / 1000 for p in front], [p["sl"] for p in front],
                "k--", alpha=0.6, label=f"Pareto 前緣（SL>={sl_accept}）", zorder=2)
    # 最高斜率線：SC = SL/cost，從原點，只考慮 SL≥sl_accept。
    if accept:
        best = max(accept, key=lambda x: x["sl"] / x["cost"])
        xmax = max(p["cost"] / 1000 for p in feas) * 1.05
        slope = best["sl"] / (best["cost"] / 1000)
        ax.plot([0, xmax], [0, slope * xmax], color="#c42f2f", alpha=0.5,
                label=f"最高 SC：{best['variant'] or best['label']+str(best['trucks'])}"
                      f"（SC={best['sl']/best['cost']*1e6:.2f}）")
        ax.scatter([best["cost"] / 1000], [best["sl"]], s=180, facecolors="none",
                   edgecolors="#c42f2f", linewidths=2, zorder=4)
    ax.axhline(sl_accept, color="#bbb", ls=":", alpha=0.7, label=f"採納門檻 SL={sl_accept}")
    ax.set_ylim(ymin, max(p["sl"] for p in feas) * 1.02 + 0.01)
    ax.set_xlim(left=-max(p["cost"] for p in feas) / 1000 * 0.03)
    ax.set_xlabel("調度成本（千 NT$/日）")
    ax.set_ylabel("ServiceLevel（借/還成功率）")
    ax.set_title(f"SC 效率前緣 — {title}")
    ax.grid(alpha=0.3); ax.legend(fontsize=8, loc="lower right")
    plt.tight_layout(); plt.savefig(out, dpi=130); plt.close()
    return out


def make_ec_chart(pts, out, ex_accept, title, true_standard=0.607):
    plt = _plt()
    feas = [p for p in pts if p["policy"] != "optimal_ub"]
    # 實測 UB 天花板（用最高 UB 優良占比，通常 UB-∞）→ EC 圖 y 上限收到此，不浪費上方空間。
    ub_pts = [p for p in pts if p["policy"] == "optimal_ub"]
    ub_ceiling = max((p["excellent"] for p in ub_pts), default=true_standard + 0.1)
    rf = next((p for p in feas if p["policy"] == "none"), None)
    rf_cost = 0.0
    rf_ex = rf["excellent"] if rf else 0.0
    priced = [p for p in feas if p["cost"] > 0]
    accept = [p for p in priced if p["excellent"] >= ex_accept]
    fig, ax = plt.subplots(figsize=(8, 6))
    _scatter(ax, feas, "excellent", dim_below=ex_accept)
    # 效率前緣：只連優良≥ex_accept。
    front = pareto_front(accept, "excellent")
    if front:
        ax.plot([p["cost"] / 1000 for p in front], [p["excellent"] for p in front],
                "k--", alpha=0.6, label=f"效率前緣（優良>={ex_accept}）", zorder=2)
    # 最高斜率（效率）線：從 none 基準起點，切到「最高 (優良−基準)/成本」且 優良≥ex_accept 的點。
    if accept and rf is not None:
        best = max(accept, key=lambda x: (x["excellent"] - rf_ex) / x["cost"])
        xmax = max(p["cost"] / 1000 for p in feas) * 1.05
        slope = (best["excellent"] - rf_ex) / (best["cost"] / 1000)
        ax.plot([0, xmax], [rf_ex, rf_ex + slope * xmax], color="#c42f2f", alpha=0.55,
                label=f"最高斜率線：{best['variant'] or best['label']+str(best['trucks'])}")
        ax.scatter([best["cost"] / 1000], [best["excellent"]], s=180, facecolors="none",
                   edgecolors="#c42f2f", linewidths=2, zorder=4)
        ax.scatter([0], [rf_ex], marker="*", s=200, color="#444", zorder=4,
                   label=f"none＝基準起點（{rf_ex:.3f}）")
    ax.axhline(ub_ceiling, color="#4d8f5f", ls="--", alpha=0.7, label=f"實測 UB 天花板 {ub_ceiling:.3f}")
    ax.axhline(true_standard, color="#4d8f5f", ls=":", label=f"真實參考 {true_standard:.3f}")
    ax.axhline(ex_accept, color="#cc9", ls=":", label=f"品質門檻 {ex_accept}")
    # y 上限收到實測 UB（+小邊距），不浪費上方空間（資本市場線自然被裁切）。
    ax.set_ylim(min(rf_ex, ex_accept) - 0.02 if rf else ex_accept - 0.02, ub_ceiling + 0.015)
    ax.set_xlabel("調度成本（千 NT$/日）")
    ax.set_ylabel("優良時段占比（EC）")
    ax.set_title(f"EC 效率前緣 — {title}")
    ax.grid(alpha=0.3); ax.legend(fontsize=8, loc="lower right")
    plt.tight_layout(); plt.savefig(out, dpi=130); plt.close()
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="weekday")
    ap.add_argument("--sl-accept", type=float, default=0.8)
    ap.add_argument("--ex-accept", type=float, default=0.55)
    ap.add_argument("--sc-ymin", type=float, default=0.7)
    ap.add_argument("--dirs", nargs="*", default=None)
    ap.add_argument("--out-prefix", default=None)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    dirs = args.dirs or [
        f"exp06_real_snapshot_{args.profile}",
        f"exp07_truck_sweep_{args.profile}",
        f"exp08_sweetspot_{args.profile}",
    ]
    pts = collect(dirs)
    if not pts:
        sys.exit(f"找不到報告點：{dirs}")
    title = args.title or f"策略間對比（{args.profile}）"
    prefix = Path(args.out_prefix) if args.out_prefix else (REPORT / f"pareto_{args.profile}")
    ts = benchmark_mean(args.profile)
    sc = make_sc_chart(pts, str(prefix) + "_sc.png", args.sl_accept, args.sc_ymin, title)
    ec = make_ec_chart(pts, str(prefix) + "_ec.png", args.ex_accept, title, true_standard=ts)
    print(f"SC 圖：{sc}\nEC 圖：{ec}（真實參考 {ts}）")
    # 文字摘要：採納門檻內最高斜率點。
    feas = [p for p in pts if p["policy"] != "optimal_ub" and p["cost"] > 0]
    sc_acc = [p for p in feas if p["sl"] >= args.sl_accept]
    ec_rf = next((p["excellent"] for p in pts if p["policy"] == "none"), 0.0)
    ec_acc = [p for p in feas if p["excellent"] >= args.ex_accept]
    if sc_acc:
        b = max(sc_acc, key=lambda x: x["sl"] / x["cost"])
        print(f"SC 最高（SL≥{args.sl_accept}）：{b['label']}{b['trucks']} SC={b['sl']/b['cost']*1e6:.2f} SL={b['sl']:.3f} 成本{b['cost']:.0f}")
    if ec_acc:
        b = max(ec_acc, key=lambda x: (x["excellent"] - ec_rf) / x["cost"])
        print(f"EC 最高斜率（優良≥{args.ex_accept}, MPT from none {ec_rf:.3f}）：{b['label']}{b['trucks']} 優良={b['excellent']:.3f} 成本{b['cost']:.0f}")


if __name__ == "__main__":
    main()
