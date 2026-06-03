"""週末 OD 平滑（§41.2）：解決週末交易稀疏(僅週間15%、站級極稀)造成的虛假難度。

**方法（已定案，見 §41.2）**：往**週間 OD** 收縮（週間同城市、資料足，隔離測試證實能修：
週末arrival+週間OD → none 0.318，對上真實「週末比週間易」）。邊際 prior 實證無效（容量結構非邊際可複製）。

    P_smooth = α·週末OD + (1−α)·週間OD   （對全部 4 個 OD 矩陣、遞迴到葉層機率分布）

α 小＝多採週間結構（週末薄資料）。weekday 不動。非破壞性輸出 transition_matrices_smoothed。

用法：python scripts/smooth_weekend_od.py --profile weekend --alpha 0.3
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLEAN = PROJECT_ROOT / "data/processed/transition_matrices_clean"
OUT = PROJECT_ROOT / "data/processed/transition_matrices_smoothed"

# 要混合的 OD 矩陣（皆 {..nested..: {leaf_key: prob}}）。
OD_FILES = [
    "station_exit_transition_by_district",
    "inter_district_transition",
    "inbound_station_transition_by_district",
    "inbound_station_transition_by_od_district",
]


def is_leaf(node: dict) -> bool:
    """葉層＝值為數字（機率分布）；否則為巢狀。"""
    return bool(node) and all(isinstance(v, (int, float)) for v in node.values())


def blend(we: dict, wd: dict, alpha: float) -> dict:
    """遞迴混合 α·we + (1−α)·wd。葉層做機率混合+正規化；巢狀層逐鍵遞迴（鍵取聯集）。

    某層 we 缺該鍵 → 等於該子樹純用 wd（週末沒資料處採週間結構），反之亦然。
    """
    we = we or {}
    wd = wd or {}
    if is_leaf(we) or is_leaf(wd):
        keys = set(we) | set(wd)
        new = {k: alpha * float(we.get(k, 0.0)) + (1 - alpha) * float(wd.get(k, 0.0)) for k in keys}
        z = sum(new.values()) or 1.0
        return {k: v / z for k, v in new.items() if v > 0}
    keys = set(we) | set(wd)
    return {k: blend(we.get(k, {}), wd.get(k, {}), alpha) for k in keys}


def inter_imbalance(prof_inter: dict) -> float:
    """逐區日淨流量失衡（占日總），用週末 arrival 的逐區外流量加權。"""
    s2d = json.load(open(CLEAN / "weekend_station_to_district.json", encoding="utf-8"))
    h = json.load(open(PROJECT_ROOT / "data/processed/arrival_rates_weekend_clean/hourly_rent_lambda_by_station.json", encoding="utf-8"))
    out: dict[str, float] = {}
    for sid, hl in h.items():
        d = s2d.get(sid)
        if d:
            out[d] = out.get(d, 0.0) + sum(float(x) for x in (hl.values() if isinstance(hl, dict) else hl))
    tot = sum(out.values()) or 1.0
    inflow = {d: 0.0 for d in out}
    for o, row in prof_inter.items():
        if o not in out:
            continue
        for d, p in row.items():
            if d in inflow:
                inflow[d] += out[o] * float(p)
    return sum(abs(inflow[d] - out[d]) for d in out) / tot


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="weekend")
    ap.add_argument("--alpha", type=float, default=0.3)
    args = ap.parse_args()
    prof, alpha = args.profile, args.alpha
    other = "weekday" if prof == "weekend" else "weekend"

    shutil.copytree(CLEAN, OUT, dirs_exist_ok=True)  # 非破壞合併；OneDrive 鎖檔安全
    for name in OD_FILES:
        we = json.load(open(CLEAN / f"{prof}_{name}.json", encoding="utf-8"))
        wd = json.load(open(CLEAN / f"{other}_{name}.json", encoding="utf-8"))
        sm = blend(we, wd, alpha)
        (OUT / f"{prof}_{name}.json").write_text(json.dumps(sm, ensure_ascii=False), encoding="utf-8")

    sm_inter = json.load(open(OUT / f"{prof}_inter_district_transition.json", encoding="utf-8"))
    print(f"[smooth] {prof} α={alpha}（往{other} OD 收縮，全 4 層）→ inter 淨流量失衡 {100*inter_imbalance(sm_inter):.1f}%（原 37.5%）")
    print(f"  輸出：{OUT}（--transition-dir 切換；{other} 檔原樣、未動）")


if __name__ == "__main__":
    main()
