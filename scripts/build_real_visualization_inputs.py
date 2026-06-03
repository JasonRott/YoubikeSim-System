"""建立真實系統地圖式視覺化所需的座標與短 ID lookup。

輸入：
- `youbike_static_info.csv`：站點 ID、名稱、行政區、經緯度。

輸出：
- station_short_id_lookup.csv/json：長站點 ID 與視覺化短 ID 的對照表。
- station_positions.json：每個站點的投影後畫布座標。
- district_positions.json：每個行政區的站點重心座標。

座標處理：
先用簡化的 local equirectangular projection 把經緯度轉成近似公尺座標，
再縮放到大型畫布。這不是 GIS 等級投影，但對台北市尺度的相對位置視覺化足夠穩定。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StationRecord:
    station_id: str
    name: str
    district: str
    latitude: float
    longitude: float


def load_static_stations(path: Path) -> list[StationRecord]:
    stations: list[StationRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            stations.append(
                StationRecord(
                    station_id=row["sno"].strip(),
                    name=row["sna"].strip(),
                    district=row["sarea"].strip(),
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                )
            )
    return sorted(stations, key=lambda station: station.station_id)


def project_to_canvas(
    stations: list[StationRecord],
    target_width: int,
    padding: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """將經緯度投影並縮放到畫布座標。"""

    mean_latitude_rad = math.radians(
        sum(station.latitude for station in stations) / len(stations)
    )
    meters_per_degree_lat = 110_540.0
    meters_per_degree_lon = 111_320.0 * math.cos(mean_latitude_rad)

    min_lat = min(station.latitude for station in stations)
    max_lat = max(station.latitude for station in stations)
    min_lon = min(station.longitude for station in stations)
    max_lon = max(station.longitude for station in stations)

    raw_positions: dict[str, tuple[float, float]] = {}
    for station in stations:
        x_meter = (station.longitude - min_lon) * meters_per_degree_lon
        # SVG/Canvas y 軸向下，所以緯度越高 y 越小。
        y_meter = (max_lat - station.latitude) * meters_per_degree_lat
        raw_positions[station.station_id] = (x_meter, y_meter)

    max_x = max(x for x, _ in raw_positions.values())
    max_y = max(y for _, y in raw_positions.values())
    scale = (target_width - 2 * padding) / max_x
    canvas_height = math.ceil(max_y * scale + 2 * padding)

    positions: dict[str, dict[str, Any]] = {}
    for station in stations:
        raw_x, raw_y = raw_positions[station.station_id]
        positions[station.station_id] = {
            "station_id": station.station_id,
            "station_name": station.name,
            "district": station.district,
            "latitude": station.latitude,
            "longitude": station.longitude,
            "x": round(raw_x * scale + padding, 3),
            "y": round(raw_y * scale + padding, 3),
        }

    metadata = {
        "projection": "local_equirectangular",
        "target_width": target_width,
        "canvas_width": target_width,
        "canvas_height": canvas_height,
        "padding": padding,
        "scale_px_per_meter": scale,
        "latitude_range": [min_lat, max_lat],
        "longitude_range": [min_lon, max_lon],
        "meters_width": max_x,
        "meters_height": max_y,
    }
    return positions, metadata


def build_short_id_lookup(
    stations: list[StationRecord],
    positions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """建立穩定短 ID，讓視覺化不用顯示冗長站點代碼。"""

    lookup: list[dict[str, Any]] = []
    for index, station in enumerate(stations, start=1):
        position = positions[station.station_id]
        lookup.append(
            {
                "short_id": f"S{index:04d}",
                "station_id": station.station_id,
                "station_name": station.name,
                "district": station.district,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "x": position["x"],
                "y": position["y"],
            }
        )
    return lookup


def build_district_positions(
    lookup_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """用行政區內所有站點的座標平均值作為行政區 node 位置。"""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in lookup_rows:
        grouped[row["district"]].append(row)

    district_positions: dict[str, dict[str, Any]] = {}
    for district, rows in sorted(grouped.items()):
        district_positions[district] = {
            "district": district,
            "station_count": len(rows),
            "latitude": sum(row["latitude"] for row in rows) / len(rows),
            "longitude": sum(row["longitude"] for row in rows) / len(rows),
            "x": round(sum(row["x"] for row in rows) / len(rows), 3),
            "y": round(sum(row["y"] for row in rows) / len(rows), 3),
        }
    return district_positions


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_lookup_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "short_id",
        "station_id",
        "station_name",
        "district",
        "latitude",
        "longitude",
        "x",
        "y",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    path: Path,
    station_count: int,
    district_positions: dict[str, dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    lines = [
        "# Real Visualization Input Build Report",
        "",
        "本報告由 `scripts/build_real_visualization_inputs.py` 產生。",
        "",
        "## 輸出內容",
        "",
        "- `station_short_id_lookup.csv/json`：長站點 ID 與短 ID 對照。",
        "- `station_positions.json`：真實站點經緯度投影後的畫布座標。",
        "- `district_positions.json`：各行政區站點重心座標。",
        "- `visualization_canvas_metadata.json`：畫布尺寸與投影比例。",
        "",
        "## 座標設定",
        "",
        f"- station count：{station_count}",
        f"- district count：{len(district_positions)}",
        f"- canvas width：{metadata['canvas_width']}",
        f"- canvas height：{metadata['canvas_height']}",
        f"- padding：{metadata['padding']}",
        f"- scale px/m：{metadata['scale_px_per_meter']:.6f}",
        f"- latitude range：{metadata['latitude_range']}",
        f"- longitude range：{metadata['longitude_range']}",
        "",
        "## 行政區重心",
        "",
    ]
    for district, row in district_positions.items():
        lines.append(
            f"- {district}：stations={row['station_count']}, "
            f"x={row['x']}, y={row['y']}"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_real_visualization_inputs(
    static_csv: Path,
    output_dir: Path,
    target_width: int,
    padding: int,
) -> dict[str, Any]:
    stations = load_static_stations(static_csv)
    station_positions, metadata = project_to_canvas(stations, target_width, padding)
    lookup_rows = build_short_id_lookup(stations, station_positions)
    short_id_by_station = {
        row["station_id"]: row["short_id"]
        for row in lookup_rows
    }
    station_id_by_short = {
        row["short_id"]: row["station_id"]
        for row in lookup_rows
    }

    for row in lookup_rows:
        station_positions[row["station_id"]]["short_id"] = row["short_id"]

    district_positions = build_district_positions(lookup_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_lookup_csv(output_dir / "station_short_id_lookup.csv", lookup_rows)
    write_json(output_dir / "station_short_id_lookup.json", lookup_rows)
    write_json(output_dir / "short_id_by_station_id.json", short_id_by_station)
    write_json(output_dir / "station_id_by_short_id.json", station_id_by_short)
    write_json(output_dir / "station_positions.json", station_positions)
    write_json(output_dir / "district_positions.json", district_positions)
    write_json(output_dir / "visualization_canvas_metadata.json", metadata)
    write_report(
        output_dir / "real_visualization_input_build_report.md",
        len(stations),
        district_positions,
        metadata,
    )
    return {
        "station_count": len(stations),
        "district_count": len(district_positions),
        "metadata": metadata,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build map-like visualization inputs from real station coordinates."
    )
    parser.add_argument(
        "--static-csv",
        type=Path,
        default=Path("data/raw/youbike_static_info.csv"),
        help="站點靜態資料 CSV。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/visualization_inputs"),
        help="輸出資料夾。",
    )
    parser.add_argument(
        "--target-width",
        type=int,
        default=8000,
        help="大型畫布寬度，單位 px。",
    )
    parser.add_argument(
        "--padding",
        type=int,
        default=160,
        help="畫布邊界留白，單位 px。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_real_visualization_inputs(
        args.static_csv,
        args.output_dir,
        args.target_width,
        args.padding,
    )
    metadata = result["metadata"]
    print(
        "visualization inputs built: "
        f"stations={result['station_count']}, "
        f"districts={result['district_count']}, "
        f"canvas={metadata['canvas_width']}x{metadata['canvas_height']}"
    )
    print(f"Visualization inputs written to: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
