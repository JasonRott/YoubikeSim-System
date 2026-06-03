"""真實快照「04:00 起跑」標準實驗矩陣 driver（零設定瑕疵、一鍵重跑、自動 replace）。

設計脈絡見 architecture_narrative §34。把所有「符合構想」的參數定死在這裡，
避免每次手動下 CLI 旗標出錯。4am 快照到手後執行此腳本即可重跑並覆蓋舊結果。

標準參數（§34 已定）：
- 起跑 04:00（start-minute=240）、值勤窗 04:00–24:00（240-1440）、時長 20h（→24:00 結束）。
- 評分窗固定 06:00–24:00（true standard 0.607 同窗）；04:00–06:00 為峰前預置/暖機段不計分。
- 主動預置時點統一 05:30/15:30（patrol-starts 與 preposition 皆 330,930，跨政策公平）。
- 守恆：集散場初始 = 車隊 − Σ快照站點 − 維修(15%)，程式自動算。
- seed 20260602。

用法：
    python scripts/run_real_matrix.py --snapshot data/snapshots/initial_bikes_4am_weekday.csv --profile weekday
    python scripts/run_real_matrix.py --snapshot data/snapshots/initial_bikes_4am_weekend.csv --profile weekend
可加 --dry-run 只印指令不執行；--skip exp07,exp08 跳過某些階段。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENARIO = PROJECT_ROOT / "scenarios" / "real_system_scenario.py"

# ---- 標準參數（§34）----
START_MINUTE = 240          # 04:00
DUTY_WINDOWS = "240-1440"   # 04:00–24:00
HOURS = 20.0                # 04:00 + 20h = 24:00
SEED = 20260602
TRUCKS_BASE = 4
SWEEP_TRUCKS = (6, 8, 10, 12)
SWEETSPOT_TRUCKS = (6, 8)

# 政策清單（exp06 全對照）：label -> (policy, trucks)
EXP06 = [
    ("real_none", "none", 4),
    ("real_p1", "fixed", 4),
    ("real_p2", "dynamic", 4),
    ("real_p3", "hybrid_anticipatory", 4),
    ("real_p4", "hybrid_smartshift", 4),
    ("real_p6", "hybrid_forecast", 4),
    ("real_p7", "pair_coord", 4),
    ("real_ub4", "optimal_ub", 4),
    ("real_ubinf", "optimal_ub", 0),
]


TRANSITION_DIR = ""  # 由 --transition-dir 設定；空＝scenario 預設 transition_matrices_clean


def build_cmd(snapshot: Path, profile: str, label: str, policy: str, trucks: int, subdir: str) -> list[str]:
    py = sys.executable
    cmd = [
        py, str(SCENARIO),
        "--hours", str(HOURS),
        "--profile", profile,
        "--snapshot-csv", str(snapshot),
        "--start-minute", str(START_MINUTE),
        "--duty-windows", DUTY_WINDOWS,
        "--seed", str(SEED),
        "--dispatch-policy", policy,
        "--trucks-per-district", str(trucks),
        "--label", label,
        "--report-subdir", subdir,
        # patrol-starts / preposition-minutes 用程式預設 330,930（§34），不另外傳。
    ]
    if TRANSITION_DIR:  # 週末用平滑 OD（§41.2）
        cmd += ["--transition-dir", TRANSITION_DIR]
    return cmd


def clear_subdir(subdir: str, dry: bool, legacy: str | None = None) -> None:
    """replace 語意：重跑前先清空該實驗子資料夾（見使用者指示『不符合構想就刪掉直接 replace』）。

    legacy：舊的 06:00 版未加 profile 後綴的資料夾名，一併刪除（04:00 版正式取代它）。
    """
    for name in (subdir, legacy):
        if not name:
            continue
        target = PROJECT_ROOT / "report" / name
        if target.exists():
            print(f"[clear] 刪除舊結果 {target}")
            if not dry:
                # OneDrive/防毒可能暫時鎖檔 → 容錯刪除（鎖住的殘檔跳過，不讓整批崩潰）。
                shutil.rmtree(target, ignore_errors=True)
                if target.exists():
                    print(f"[clear] 警告：{target} 有鎖住的殘檔未刪（OneDrive？），不影響新結果寫入。")


def run_stage(name: str, subdir: str, jobs: list[tuple[str, str, int]], snapshot: Path, profile: str, dry: bool, legacy: str | None = None) -> None:
    print(f"\n===== {name}（{subdir}）=====")
    clear_subdir(subdir, dry, legacy)
    for label, policy, trucks in jobs:
        cmd = build_cmd(snapshot, profile, label, policy, trucks, subdir)
        print(f"  -> {label}: {policy} trucks={trucks}")
        if dry:
            print("     " + " ".join(cmd))
            continue
        # 用 utf-8 解碼子程序輸出（scenario 以 utf-8 印中文/− 等字元）；
        # 避免 Windows cp950 預設解碼在這些字元上拋 UnicodeDecodeError 而中斷整批。
        res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        out = res.stdout or ""
        tail = [ln for ln in out.splitlines() if any(k in ln for k in ("Report written", "snapshot", "Traceback", "Error"))]
        for ln in tail[-3:]:
            print("     " + ln)
        if res.returncode != 0:
            print(f"     !! 失敗 rc={res.returncode}")
            print(res.stderr[-800:])


def main() -> None:
    # Windows 終端 cp950 無法編碼中文/− 等字元 → 改 utf-8，避免 driver 自身 print 崩潰。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="真實快照 04:00 標準實驗矩陣（§34）")
    ap.add_argument("--snapshot", type=Path, required=True, help="04:00 站點快照 CSV/JSON")
    ap.add_argument("--profile", choices=["weekday", "weekend"], default="weekday")
    ap.add_argument("--dry-run", action="store_true", help="只印指令不執行")
    ap.add_argument("--skip", type=str, default="", help="跳過階段，逗號分隔：exp06,exp07,exp08")
    ap.add_argument("--transition-dir", type=str, default="", help="OD 目錄（週末用平滑 OD：data/processed/transition_matrices_smoothed）。")
    args = ap.parse_args()
    global TRANSITION_DIR
    TRANSITION_DIR = args.transition_dir

    if not args.snapshot.exists():
        sys.exit(f"快照檔不存在：{args.snapshot}（請確認 4am 快照已到位）")
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    tag = args.profile

    # legacy：06:00 舊版（未加 profile 後綴）。僅 weekday 重跑時順手清除（04:00 正式取代）。
    leg = lambda base: (base if args.profile == "weekday" else None)
    if "exp06" not in skip:
        run_stage("exp06 全對照 6-way + UB", f"exp06_real_snapshot_{tag}", EXP06, args.snapshot, args.profile, args.dry_run, leg("exp06_real_snapshot"))
    if "exp07" not in skip:
        jobs = []
        for n in SWEEP_TRUCKS:
            jobs.append((f"p3_t{n}", "hybrid_anticipatory", n))
            jobs.append((f"p7_t{n}", "pair_coord", n))
            jobs.append((f"ub_t{n}", "optimal_ub", n))
        run_stage("exp07 車數掃描 UB+P3+P7", f"exp07_truck_sweep_{tag}", jobs, args.snapshot, args.profile, args.dry_run, leg("exp07_truck_sweep"))
    if "exp08" not in skip:
        jobs = []
        for n in SWEETSPOT_TRUCKS:
            jobs.append((f"p2_t{n}", "dynamic", n))
            jobs.append((f"p6_t{n}", "hybrid_forecast", n))
        run_stage("exp08 甜蜜點 P2/P6", f"exp08_sweetspot_{tag}", jobs, args.snapshot, args.profile, args.dry_run, leg("exp08_sweetspot"))

    print("\n全部完成。" if not args.dry_run else "\n(dry-run 結束)")


if __name__ == "__main__":
    main()
