"""從真實營運資料建立每站 capacity 與 initial bikes。

輸入：
- `youbike_dynamic_2026-04-28.csv`：實時資料，`Quantity` 欄為該站總停車格數（= capacity）。
  經檢查，`Quantity` 在當日各時間快照中固定不變，故每站取一個值即可。
- `kpis_1.csv`：`avg_fill_ratio`（平均填充率）、`bike_avail_rate`（見車率）、
  `dock_avail_rate`（見位率）。initial bikes = round(capacity * avg_fill_ratio)。
- `youbike_static_info.csv`：界定要納入的 1739 個正式站點。

輸出：
- `data/processed/station_capacity/station_capacity.json`
  { station_id: {capacity, initial_bikes, avg_fill_ratio, bike_avail_rate, dock_avail_rate} }
- `data/processed/station_capacity/station_capacity_build_report.md`
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_static_station_ids(static_csv: Path) -> list[str]:
    ids: list[str] = []
    with static_csv.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            ids.append(row["sno"].strip())
    return sorted(set(ids))


def load_capacity_from_dynamic(dynamic_csv: Path) -> dict[str, int]:
    """每站取一個 Quantity 當作 capacity（當日各快照固定不變）。"""

    capacity: dict[str, int] = {}
    with dynamic_csv.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            sno = row["sno"].strip()
            if sno in capacity:
                continue
            quantity = row.get("Quantity")
            if quantity in (None, ""):
                continue
            capacity[sno] = int(float(quantity))
    return capacity


def load_kpis(kpis_csv: Path) -> dict[str, dict[str, float]]:
    kpis: dict[str, dict[str, float]] = {}
    with kpis_csv.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            sno = row["sno"].strip()
            kpis[sno] = {
                "avg_fill_ratio": float(row["avg_fill_ratio"]),
                "bike_avail_rate": float(row["bike_avail_rate"]),
                "dock_avail_rate": float(row["dock_avail_rate"]),
            }
    return kpis


def build_capacity_inputs(
    static_csv: Path,
    dynamic_csv: Path,
    kpis_csv: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    station_ids = load_static_station_ids(static_csv)
    capacity = load_capacity_from_dynamic(dynamic_csv)
    kpis = load_kpis(kpis_csv)

    result: dict[str, dict[str, Any]] = {}
    missing_capacity: list[str] = []
    missing_kpi: list[str] = []

    for station_id in station_ids:
        cap = capacity.get(station_id)
        if cap is None:
            missing_capacity.append(station_id)
            continue
        kpi = kpis.get(station_id)
        if kpi is None:
            missing_kpi.append(station_id)
            fill_ratio = 0.5
            bike_rate = None
            dock_rate = None
        else:
            fill_ratio = kpi["avg_fill_ratio"]
            bike_rate = kpi["bike_avail_rate"]
            dock_rate = kpi["dock_avail_rate"]

        initial_bikes = int(round(cap * fill_ratio))
        initial_bikes = max(0, min(cap, initial_bikes))
        result[station_id] = {
            "capacity": cap,
            "initial_bikes": initial_bikes,
            "avg_fill_ratio": round(fill_ratio, 4),
            "bike_avail_rate": bike_rate,
            "dock_avail_rate": dock_rate,
        }

    caps = [v["capacity"] for v in result.values()]
    inits = [v["initial_bikes"] for v in result.values()]
    report = {
        "station_count": len(result),
        "missing_capacity": missing_capacity,
        "missing_kpi": missing_kpi,
        "capacity_min": min(caps) if caps else None,
        "capacity_max": max(caps) if caps else None,
        "capacity_mean": round(sum(caps) / len(caps), 2) if caps else None,
        "initial_bikes_mean": round(sum(inits) / len(inits), 2) if inits else None,
    }
    return result, report


def write_report(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Station Capacity Build Report",
        "",
        "本報告由 `scripts/build_station_capacity_inputs.py` 產生。",
        "",
        "## 來源",
        "",
        "- capacity：`youbike_dynamic_2026-04-28.csv` 的 `Quantity`（每站總停車格，當日固定）。",
        "- initial bikes：`round(capacity * avg_fill_ratio)`，avg_fill_ratio 來自 `kpis_1.csv`。",
        "- 見車率 / 見位率：`kpis_1.csv` 的 `bike_avail_rate` / `dock_avail_rate`，保留作為真實 benchmark。",
        "",
        "## 摘要",
        "",
        f"- 納入站點數：{report['station_count']}",
        f"- capacity 範圍：{report['capacity_min']} ~ {report['capacity_max']}，平均 {report['capacity_mean']}",
        f"- initial bikes 平均：{report['initial_bikes_mean']}",
        f"- 缺 capacity 的站點數：{len(report['missing_capacity'])}",
        f"- 缺 KPI（改用 fill=0.5）的站點數：{len(report['missing_kpi'])}",
    ]
    if report["missing_capacity"]:
        lines.append(f"- 缺 capacity 站點：{report['missing_capacity']}")
    if report["missing_kpi"]:
        lines.append(f"- 缺 KPI 站點：{report['missing_kpi']}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build per-station real capacity and initial bikes."
    )
    parser.add_argument("--static-csv", type=Path, default=PROJECT_ROOT / "data/raw/youbike_static_info.csv")
    parser.add_argument(
        "--dynamic-csv", type=Path, default=PROJECT_ROOT / "data/raw/youbike_dynamic_2026-04-28.csv"
    )
    parser.add_argument("--kpis-csv", type=Path, default=PROJECT_ROOT / "data/raw/kpis_1.csv")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data/processed/station_capacity"
    )
    args = parser.parse_args()

    result, report = build_capacity_inputs(args.static_csv, args.dynamic_csv, args.kpis_csv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "station_capacity.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    write_report(args.output_dir / "station_capacity_build_report.md", report)
    print(
        f"station capacity written: {report['station_count']} stations, "
        f"capacity {report['capacity_min']}~{report['capacity_max']} "
        f"(mean {report['capacity_mean']}), initial mean {report['initial_bikes_mean']}"
    )
    if report["missing_capacity"] or report["missing_kpi"]:
        print(
            f"WARNING: missing_capacity={len(report['missing_capacity'])}, "
            f"missing_kpi={len(report['missing_kpi'])}"
        )


if __name__ == "__main__":
    main()
