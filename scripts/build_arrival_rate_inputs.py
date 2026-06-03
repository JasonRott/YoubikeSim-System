"""將站點 2 小時 arrival rate 轉成模擬可直接取用的 hourly lambda。

輸入檔 `stations_arrival_rates_2hr.csv` 的欄位：
- sno：YouBike 站點 ID。
- time_period：2 小時區間，例如 08:00-10:00。
- lambda_rent_hr：該 2 小時區間內，每小時平均租借到達率。
- lambda_return_hr：該 2 小時區間內，每小時平均還車到達率。

Baseline 的 demand_generator 目前需要的是「每站、每小時」的租借到達率。
因此本腳本會把 2 小時區間展開成 24 個 hourly lambda：
08:00-10:00 的 lambda 會同時套用到 hour 8 與 hour 9。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXPECTED_PERIODS = [
    "00:00-02:00",
    "02:00-04:00",
    "04:00-06:00",
    "06:00-08:00",
    "08:00-10:00",
    "10:00-12:00",
    "12:00-14:00",
    "14:00-16:00",
    "16:00-18:00",
    "18:00-20:00",
    "20:00-22:00",
    "22:00-24:00",
]


@dataclass(frozen=True)
class ArrivalRateRow:
    """一列 2 小時 arrival rate 原始資料。"""

    station_id: str
    time_period: str
    lambda_rent_hr: float
    lambda_return_hr: float


def parse_period_hours(time_period: str) -> list[int]:
    """把 `08:00-10:00` 轉成 `[8, 9]`。

    目前輸入固定為 2 小時區間；若未來改成 1 小時或 3 小時，
    這個函式也可以自然展開成對應小時。
    """

    start_text, end_text = time_period.split("-")
    start_hour = int(start_text.split(":")[0])
    end_hour = int(end_text.split(":")[0])
    if end_hour == 24:
        return list(range(start_hour, 24))
    if not 0 <= start_hour < end_hour <= 24:
        raise ValueError(f"不合法的 time_period：{time_period}")
    return list(range(start_hour, end_hour))


def load_static_station_info(path: Path) -> dict[str, dict[str, str]]:
    """讀取站點靜態資料，補上站名與行政區，方便後續 debug。"""

    stations: dict[str, dict[str, str]] = {}
    if not path.exists():
        return stations

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            station_id = row["sno"].strip()
            stations[station_id] = {
                "station_name": row.get("sna", "").strip(),
                "district": row.get("sarea", "").strip(),
                "latitude": row.get("latitude", "").strip(),
                "longitude": row.get("longitude", "").strip(),
            }
    return stations


def load_arrival_rate_rows(path: Path) -> list[ArrivalRateRow]:
    """讀取並做基本型別轉換。"""

    rows: list[ArrivalRateRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            station_id = row["sno"].strip()
            time_period = row["time_period"].strip()
            lambda_rent_hr = float(row["lambda_rent_hr"])
            lambda_return_hr = float(row["lambda_return_hr"])
            if lambda_rent_hr < 0 or lambda_return_hr < 0:
                raise ValueError(f"lambda 不可為負數：{row}")
            rows.append(
                ArrivalRateRow(
                    station_id=station_id,
                    time_period=time_period,
                    lambda_rent_hr=lambda_rent_hr,
                    lambda_return_hr=lambda_return_hr,
                )
            )
    return rows


def load_excluded_station_ids(path: Path | None) -> dict[str, str]:
    """讀取需要排除的站點 ID 與原因。"""

    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    excluded = data.get("excluded_station_ids", {})
    if isinstance(excluded, list):
        return {str(station_id): "" for station_id in excluded}
    return {str(station_id): str(reason) for station_id, reason in excluded.items()}


def build_hourly_rates(
    rows: list[ArrivalRateRow],
    static_stations: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """建立 hourly JSON/CSV 所需的資料結構。"""

    rent_lambda_by_station: dict[str, dict[str, float]] = defaultdict(dict)
    return_lambda_by_station: dict[str, dict[str, float]] = defaultdict(dict)
    source_period_by_station: dict[str, dict[str, str]] = defaultdict(dict)
    periods_by_station: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        periods_by_station[row.station_id].add(row.time_period)
        for hour in parse_period_hours(row.time_period):
            hour_key = str(hour)
            if hour_key in rent_lambda_by_station[row.station_id]:
                raise ValueError(
                    f"站點 {row.station_id} 的 hour {hour} 被多個 time_period 覆蓋。"
                )
            rent_lambda_by_station[row.station_id][hour_key] = row.lambda_rent_hr
            return_lambda_by_station[row.station_id][hour_key] = row.lambda_return_hr
            source_period_by_station[row.station_id][hour_key] = row.time_period

    missing_hours_by_station = {
        station_id: sorted(set(map(str, range(24))) - set(hourly_rates))
        for station_id, hourly_rates in rent_lambda_by_station.items()
        if len(hourly_rates) != 24
    }
    incomplete_period_stations = {
        station_id: sorted(EXPECTED_PERIODS_SET - periods)
        for station_id, periods in periods_by_station.items()
        if periods != EXPECTED_PERIODS_SET
    }
    station_ids = set(rent_lambda_by_station)
    static_station_ids = set(static_stations)

    hourly_rows: list[dict[str, Any]] = []
    for station_id in sorted(station_ids):
        station_info = static_stations.get(station_id, {})
        for hour in range(24):
            hour_key = str(hour)
            hourly_rows.append(
                {
                    "sno": station_id,
                    "station_name": station_info.get("station_name", ""),
                    "district": station_info.get("district", ""),
                    "hour": hour,
                    "source_time_period": source_period_by_station[station_id][hour_key],
                    "lambda_rent_hr": rent_lambda_by_station[station_id][hour_key],
                    "lambda_return_hr": return_lambda_by_station[station_id][hour_key],
                }
            )

    metadata = {
        "source_granularity": "2hr",
        "output_granularity": "1hr",
        "lambda_unit": "arrivals_per_hour",
        "source_row_count": len(rows),
        "station_count_in_rate_file": len(station_ids),
        "station_count_in_static_file": len(static_station_ids),
        "stations_in_rate_not_in_static": sorted(station_ids - static_station_ids),
        "stations_in_static_not_in_rate": sorted(static_station_ids - station_ids),
        "missing_hours_station_count": len(missing_hours_by_station),
        "incomplete_period_station_count": len(incomplete_period_stations),
        "time_periods": EXPECTED_PERIODS,
        "max_lambda_rent_hr": max(
            row.lambda_rent_hr for row in rows
        )
        if rows
        else 0,
        "max_lambda_return_hr": max(
            row.lambda_return_hr for row in rows
        )
        if rows
        else 0,
    }

    return {
        "metadata": metadata,
        "rent_lambda_by_station": {
            station_id: dict(sorted(hourly.items(), key=lambda item: int(item[0])))
            for station_id, hourly in sorted(rent_lambda_by_station.items())
        },
        "return_lambda_by_station": {
            station_id: dict(sorted(hourly.items(), key=lambda item: int(item[0])))
            for station_id, hourly in sorted(return_lambda_by_station.items())
        },
        "source_period_by_station": {
            station_id: dict(sorted(hourly.items(), key=lambda item: int(item[0])))
            for station_id, hourly in sorted(source_period_by_station.items())
        },
        "hourly_rows": hourly_rows,
        "missing_hours_by_station": missing_hours_by_station,
        "incomplete_period_stations": incomplete_period_stations,
    }


EXPECTED_PERIODS_SET = set(EXPECTED_PERIODS)


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_hourly_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "sno",
        "station_name",
        "district",
        "hour",
        "source_time_period",
        "lambda_rent_hr",
        "lambda_return_hr",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, result: dict[str, Any]) -> None:
    metadata = result["metadata"]
    lines = [
        "# Arrival Rate Build Report",
        "",
        "本報告由 `scripts/build_arrival_rate_inputs.py` 產生。",
        "",
        "## 轉換邏輯",
        "",
        "`stations_arrival_rates_2hr.csv` 已提供每 2 小時區間的每小時到達率。",
        "因此轉換成 hourly lambda 時，會把同一個 2 小時區間的 lambda 複製到兩個小時。",
        "",
        "例如：",
        "",
        "```text",
        "08:00-10:00, lambda_rent_hr = 43.18",
        "=> hour 8 = 43.18, hour 9 = 43.18",
        "```",
        "",
        "## 輸出檔案",
        "",
        "- `hourly_rent_lambda_by_station.json`：Baseline `demand_generator` 可直接使用的租借到達率。",
        "- `hourly_return_lambda_by_station.json`：還車到達率，暫作校準與後續驗證使用。",
        "- `hourly_arrival_rates_long.csv`：長表格式，方便人工檢查。",
        "- `arrival_rate_metadata.json`：轉換摘要與資料品質檢查。",
        "",
        "## 資料摘要",
        "",
        f"- 原始 row 數：{metadata['source_row_count']}",
        f"- rate 檔站點數：{metadata['station_count_in_rate_file']}",
        f"- static 檔站點數：{metadata['station_count_in_static_file']}",
        f"- rate 有但 static 沒有的站點數：{len(metadata['stations_in_rate_not_in_static'])}",
        f"- static 有但 rate 沒有的站點數：{len(metadata['stations_in_static_not_in_rate'])}",
        f"- 缺少 hourly lambda 的站點數：{metadata['missing_hours_station_count']}",
        f"- 缺少 2 小時區間的站點數：{metadata['incomplete_period_station_count']}",
        f"- 最大 lambda_rent_hr：{metadata['max_lambda_rent_hr']}",
        f"- 最大 lambda_return_hr：{metadata['max_lambda_return_hr']}",
        f"- 已排除站點數：{len(metadata['excluded_station_ids'])}",
        "",
    ]

    if metadata["excluded_station_ids"]:
        lines.extend(["## 已排除站點", ""])
        for station_id, reason in metadata["excluded_station_ids"].items():
            lines.append(f"- {station_id}：{reason or '未提供原因'}")
        lines.append("")

    if metadata["stations_in_rate_not_in_static"]:
        lines.extend(
            [
                "## Rate 檔有但 Static 檔沒有的站點",
                "",
                ", ".join(metadata["stations_in_rate_not_in_static"]),
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_arrival_rate_inputs(
    input_csv: Path,
    static_csv: Path,
    output_dir: Path,
    excluded_stations_json: Path | None = None,
) -> dict[str, Any]:
    static_stations = load_static_station_info(static_csv)
    excluded_station_ids = load_excluded_station_ids(excluded_stations_json)
    rows = [
        row
        for row in load_arrival_rate_rows(input_csv)
        if row.station_id not in excluded_station_ids
    ]
    period_counts = Counter(row.time_period for row in rows)
    unknown_periods = sorted(set(period_counts) - EXPECTED_PERIODS_SET)
    if unknown_periods:
        raise ValueError(f"遇到未預期的 time_period：{unknown_periods}")

    result = build_hourly_rates(rows, static_stations)
    result["metadata"]["excluded_station_ids"] = excluded_station_ids
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        output_dir / "hourly_rent_lambda_by_station.json",
        result["rent_lambda_by_station"],
    )
    write_json(
        output_dir / "hourly_return_lambda_by_station.json",
        result["return_lambda_by_station"],
    )
    write_json(
        output_dir / "hourly_source_period_by_station.json",
        result["source_period_by_station"],
    )
    write_json(output_dir / "arrival_rate_metadata.json", result["metadata"])
    write_hourly_csv(output_dir / "hourly_arrival_rates_long.csv", result["hourly_rows"])
    write_report(output_dir / "arrival_rate_build_report.md", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build hourly station arrival-rate inputs from 2-hour lambda CSV."
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path("data/raw/stations_arrival_rates_2hr.csv"),
        help="組員提供的 2 小時 arrival rate CSV。",
    )
    parser.add_argument(
        "--static-csv",
        type=Path,
        default=Path("data/raw/youbike_static_info.csv"),
        help="站點靜態資料 CSV，用於補站名與行政區。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/arrival_rates"),
        help="輸出資料夾。",
    )
    parser.add_argument(
        "--excluded-stations-json",
        type=Path,
        default=None,
        help="包含 excluded_station_ids 的 JSON 清理規則。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_arrival_rate_inputs(
        args.input_csv,
        args.static_csv,
        args.output_dir,
        args.excluded_stations_json,
    )
    metadata = result["metadata"]
    print(
        "arrival rates built: "
        f"stations={metadata['station_count_in_rate_file']}, "
        f"rows={metadata['source_row_count']}, "
        f"missing_hours={metadata['missing_hours_station_count']}"
    )
    print(f"Arrival-rate inputs written to: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
