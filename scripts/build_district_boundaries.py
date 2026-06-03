"""把台北市行政區界 Shapefile 轉成視覺化畫布座標。

輸入：
- `Taipei_district_graph/G97_A_CADIST_P.shp`（TWD97 / TM2 zone 121, EPSG:3826, 公尺）
- `youbike_static_info.csv`（重算平均緯度，確保投影與站點一致）
- `data/processed/visualization_inputs/visualization_canvas_metadata.json`
  （沿用站點投影的 min_lon / max_lat / scale / padding）

處理：
1. 用 pyproj 把每個 polygon 頂點 TWD97 TM2 (EPSG:3826) → WGS84 經緯度 (EPSG:4326)。
2. 套用與 `build_real_visualization_inputs.py` 完全相同的 local equirectangular 投影，
   把經緯度轉成畫布座標，確保邊界與站點精準對齊。

輸出：
- `data/processed/visualization_inputs/district_boundaries.json`
  格式：{ 行政區名: [ [ [x, y], ... ] (一個 ring), ... ] }
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import shapefile  # pyshp
from pyproj import Transformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
METERS_PER_DEGREE_LAT = 110_540.0
METERS_PER_DEGREE_LON_EQUATOR = 111_320.0


def mean_latitude_from_static(static_csv: Path) -> float:
    latitudes: list[float] = []
    with static_csv.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            latitudes.append(float(row["latitude"]))
    return sum(latitudes) / len(latitudes)


def load_station_districts(static_csv: Path) -> set[str]:
    districts: set[str] = set()
    with static_csv.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            districts.add(row["sarea"].strip())
    return districts


def make_projector(static_csv: Path, metadata: dict[str, Any]):
    """回傳一個把 (lon, lat) 轉成畫布 (x, y) 的函式，與站點投影完全一致。"""

    mean_lat_rad = math.radians(mean_latitude_from_static(static_csv))
    meters_per_degree_lon = METERS_PER_DEGREE_LON_EQUATOR * math.cos(mean_lat_rad)
    min_lon = metadata["longitude_range"][0]
    max_lat = metadata["latitude_range"][1]
    scale = metadata["scale_px_per_meter"]
    padding = metadata["padding"]

    def project(lon: float, lat: float) -> tuple[float, float]:
        x_meter = (lon - min_lon) * meters_per_degree_lon
        y_meter = (max_lat - lat) * METERS_PER_DEGREE_LAT
        return (
            round(x_meter * scale + padding, 2),
            round(y_meter * scale + padding, 2),
        )

    return project


def decimate(points: list[list[float]], max_points: int) -> list[list[float]]:
    """等距抽稀過密的環，保留首尾，降低輸出檔大小。"""

    if max_points <= 0 or len(points) <= max_points:
        return points
    step = len(points) / max_points
    kept = [points[int(i * step)] for i in range(max_points)]
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept


def build_boundaries(
    shapefile_path: Path,
    static_csv: Path,
    metadata: dict[str, Any],
    max_points_per_ring: int,
) -> tuple[dict[str, list[list[list[float]]]], list[str]]:
    project = make_projector(static_csv, metadata)
    transformer = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
    station_districts = load_station_districts(static_csv)

    reader = shapefile.Reader(str(shapefile_path), encoding="utf-8")
    boundaries: dict[str, list[list[list[float]]]] = {}
    unmatched: list[str] = []

    for shape, record in zip(reader.shapes(), reader.records()):
        attributes = record.as_dict()
        name = (attributes.get("TNAME") or attributes.get("PTNAME") or "").strip()
        if name not in station_districts:
            unmatched.append(name)

        # parts 把單一 shape 切成多個 ring；最後一個 ring 結尾用點數補齊。
        starts = list(shape.parts) + [len(shape.points)]
        rings: list[list[list[float]]] = []
        for index in range(len(shape.parts)):
            ring_points = shape.points[starts[index] : starts[index + 1]]
            canvas_points: list[list[float]] = []
            for easting, northing in ring_points:
                lon, lat = transformer.transform(easting, northing)
                x, y = project(lon, lat)
                canvas_points.append([x, y])
            canvas_points = decimate(canvas_points, max_points_per_ring)
            if len(canvas_points) >= 3:
                rings.append(canvas_points)
        if rings:
            boundaries.setdefault(name, []).extend(rings)

    return boundaries, unmatched


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Taipei district boundary canvas polylines for the real map visualization."
    )
    parser.add_argument(
        "--shapefile",
        type=Path,
        default=PROJECT_ROOT / "data/geo/Taipei_district_graph" / "G97_A_CADIST_P.shp",
    )
    parser.add_argument(
        "--static-csv",
        type=Path,
        default=PROJECT_ROOT / "data/raw/youbike_static_info.csv",
    )
    parser.add_argument(
        "--visualization-input-dir",
        type=Path,
        default=PROJECT_ROOT / "data/processed/visualization_inputs",
    )
    parser.add_argument(
        "--max-points-per-ring",
        type=int,
        default=0,
        help=(
            "每個環最多保留的頂點數，過密時等距抽稀；0 表示不抽稀。"
            "預設不抽稀，因為抽稀會讓相鄰行政區的共用邊界取到不同點、"
            "在交界出現複數錯開的線；保留原始頂點可讓共用邊界精準重疊成單一線。"
        ),
    )
    args = parser.parse_args()

    metadata = json.loads(
        (args.visualization_input_dir / "visualization_canvas_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    boundaries, unmatched = build_boundaries(
        args.shapefile,
        args.static_csv,
        metadata,
        args.max_points_per_ring,
    )

    output_path = args.visualization_input_dir / "district_boundaries.json"
    output_path.write_text(
        json.dumps(boundaries, ensure_ascii=False), encoding="utf-8"
    )

    total_points = sum(len(ring) for rings in boundaries.values() for ring in rings)
    print(f"district boundaries written to: {output_path}")
    print(f"districts: {len(boundaries)}, total points: {total_points}")
    if unmatched:
        print(f"WARNING: names not found in station districts: {unmatched}")


if __name__ == "__main__":
    main()
