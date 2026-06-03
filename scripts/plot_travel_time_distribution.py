"""檢視一次模擬的旅行時間與 OD 距離分布，輸出分布圖與摘要。

用法：
    python scripts/plot_travel_time_distribution.py [--report-dir <dir>]

未指定 --report-dir 時，使用 report/ 下最新的 real_system_* 報告。
輸出 `travel_time_distribution.png` 到該報告資料夾。
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import statistics
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def latest_report(report_root: Path) -> Path:
    candidates = sorted(glob.glob(str(report_root / "real_system_*")))
    if not candidates:
        raise FileNotFoundError("no real_system report found")
    return Path(candidates[-1])


def euclidean_km(positions, origin, destination):
    a = positions.get(origin)
    b = positions.get(destination)
    if not a or not b:
        return None
    mean_lat = math.radians((a["latitude"] + b["latitude"]) / 2)
    dx = (b["longitude"] - a["longitude"]) * 111320 * math.cos(mean_lat)
    dy = (b["latitude"] - a["latitude"]) * 110540
    return math.hypot(dx, dy) / 1000


def percentile(values, p):
    values = sorted(values)
    return values[int(p / 100 * (len(values) - 1))]


def main() -> None:
    import json

    parser = argparse.ArgumentParser(description="Plot travel-time / distance distribution for a run.")
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument(
        "--station-positions",
        type=Path,
        default=PROJECT_ROOT / "data/processed/visualization_inputs/station_positions.json",
    )
    args = parser.parse_args()

    report_dir = args.report_dir or latest_report(PROJECT_ROOT / "report")
    positions = json.loads(args.station_positions.read_text(encoding="utf-8"))

    rows = [
        r
        for r in csv.DictReader((report_dir / "events.csv").open(encoding="utf-8"))
        if r["event_type"] == "route_planned" and r.get("total_travel_time")
    ]
    times = [float(r["total_travel_time"]) for r in rows]
    dists = [euclidean_km(positions, r["origin_station_id"], r["destination_station_id"]) for r in rows]
    dists = [x for x in dists if x is not None]
    same = [float(r["total_travel_time"]) for r in rows if r["origin_district_id"] == r["destination_district_id"]]
    cross = [float(r["total_travel_time"]) for r in rows if r["origin_district_id"] != r["destination_district_id"]]
    fallback = Counter(
        r.get("reason", "")
        for r in csv.DictReader((report_dir / "events.csv").open(encoding="utf-8"))
        if r["event_type"] == "route_planner_fallback"
    )

    print("report:", os.path.basename(str(report_dir)))
    print("fallback reasons:", dict(fallback))
    print(
        "travel time (min): n=%d mean=%.2f median=%.2f p90=%.1f p99=%.1f max=%.1f"
        % (len(times), statistics.mean(times), statistics.median(times),
           percentile(times, 90), percentile(times, 99), max(times))
    )
    print(
        "same-district %.0f%% mean=%.2f | cross-district %.0f%% mean=%.2f"
        % (len(same) / len(times) * 100, statistics.mean(same),
           len(cross) / len(times) * 100, statistics.mean(cross))
    )
    print(
        "euclidean km: mean=%.3f median=%.3f p90=%.2f max=%.2f"
        % (statistics.mean(dists), statistics.median(dists), percentile(dists, 90), max(dists))
    )

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    ax[0].hist(times, bins=60, color="#2f78b5", edgecolor="white")
    ax[0].axvline(statistics.mean(times), color="#c42f2f", ls="--", label="mean %.1f" % statistics.mean(times))
    ax[0].axvline(statistics.median(times), color="#4d8f5f", ls="--", label="median %.1f" % statistics.median(times))
    ax[0].set_title("Travel time distribution (min)")
    ax[0].set_xlabel("minutes")
    ax[0].legend()
    ax[1].hist(dists, bins=60, color="#7f5fa0", edgecolor="white")
    ax[1].set_title("OD euclidean distance (km)")
    ax[1].set_xlabel("km")
    plt.tight_layout()
    out = report_dir / "travel_time_distribution.png"
    plt.savefig(out, dpi=110)
    print("saved plot:", out)


if __name__ == "__main__":
    main()
