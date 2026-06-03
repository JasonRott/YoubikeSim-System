"""檢查真實 YouBike 資料是否能接入目前 baseline 模擬系統。

此腳本負責回答兩個問題：
1. 哪些站點出現在 arrival rate 檔，但不存在於 static station 檔？
2. 已處理的 station registry、arrival rate、transition matrix 是否能被目前
   `Station`、`DummyNode`、`BaselineModel`、`demand_generator` 採納？

注意：目前 baseline 的預設 `plan_route()` 是 district-first routing。
我們已建立的真實 OD matrix 則更接近 station-first routing：
先由起點站決定「同區目的站」或「區外 dummy node」。
因此本腳本會明確標示哪些資料可直接採納，哪些需要下一階段 route planner 擴充。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import simpy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from youbike_sim import (
    BaselineModel,
    DummyNode,
    Station,
    constant_travel_times,
    demand_generator,
    load_hourly_lambda_by_station,
    load_station_exit_route_planner,
)


@dataclass(frozen=True)
class StaticStation:
    """static station registry 中的一個站點。"""

    station_id: str
    name: str
    district: str
    latitude: float
    longitude: float


def load_static_stations(path: Path) -> dict[str, StaticStation]:
    stations: dict[str, StaticStation] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            station_id = row["sno"].strip()
            stations[station_id] = StaticStation(
                station_id=station_id,
                name=row["sna"].strip(),
                district=row["sarea"].strip(),
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
            )
    return stations


def load_arrival_rate_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def analyze_missing_rate_stations(
    missing_station_ids: list[str],
    arrival_rows: list[dict[str, str]],
    geojson_paths: list[Path],
) -> list[dict[str, Any]]:
    """整理 rate 有但 static 沒有的站點在 rate 與 OD 中的出現狀況。"""

    rates_by_station: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in arrival_rows:
        rates_by_station[row["sno"].strip()].append(row)

    od_summary: dict[str, dict[str, Any]] = {
        station_id: {
            "weekday_origin_count": 0,
            "weekday_destination_count": 0,
            "weekend_origin_count": 0,
            "weekend_destination_count": 0,
            "observed_names": set(),
            "observed_districts": set(),
        }
        for station_id in missing_station_ids
    }

    for geojson_path in geojson_paths:
        profile = "weekend" if "週末" in geojson_path.name else "weekday"
        data = load_json(geojson_path)
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            count = int(props["sum_of_txn_times"])
            origin_id = str(props["on_stop_id"]).strip()
            destination_id = str(props["off_stop_id"]).strip()

            if origin_id in od_summary:
                od_summary[origin_id][f"{profile}_origin_count"] += count
                od_summary[origin_id]["observed_names"].add(str(props.get("on_stop", "")))
                od_summary[origin_id]["observed_districts"].add(
                    str(props.get("district_origin", ""))
                )
            if destination_id in od_summary:
                od_summary[destination_id][f"{profile}_destination_count"] += count
                od_summary[destination_id]["observed_names"].add(
                    str(props.get("off_stop", ""))
                )
                od_summary[destination_id]["observed_districts"].add(
                    str(props.get("district_destination", ""))
                )

    results: list[dict[str, Any]] = []
    for station_id in missing_station_ids:
        rows = rates_by_station[station_id]
        rent_values = [float(row["lambda_rent_hr"]) for row in rows]
        return_values = [float(row["lambda_return_hr"]) for row in rows]
        od = od_summary[station_id]
        results.append(
            {
                "station_id": station_id,
                "rate_period_count": len(rows),
                "avg_lambda_rent_hr": sum(rent_values) / len(rent_values),
                "max_lambda_rent_hr": max(rent_values),
                "avg_lambda_return_hr": sum(return_values) / len(return_values),
                "max_lambda_return_hr": max(return_values),
                "weekday_origin_count": od["weekday_origin_count"],
                "weekday_destination_count": od["weekday_destination_count"],
                "weekend_origin_count": od["weekend_origin_count"],
                "weekend_destination_count": od["weekend_destination_count"],
                "observed_names": sorted(od["observed_names"]),
                "observed_districts": sorted(od["observed_districts"]),
            }
        )
    return results


def collect_transition_station_ids(transition_dir: Path, profile: str) -> dict[str, set[str]]:
    """蒐集 transition matrix 內出現的站點 ID。"""

    station_exit = load_json(
        transition_dir / f"{profile}_station_exit_transition_by_district.json"
    )
    inbound = load_json(
        transition_dir / f"{profile}_inbound_station_transition_by_district.json"
    )
    inbound_by_od = load_json(
        transition_dir / f"{profile}_inbound_station_transition_by_od_district.json"
    )

    origin_ids: set[str] = set()
    destination_ids: set[str] = set()

    for station_rows in station_exit.values():
        for origin_id, destinations in station_rows.items():
            origin_ids.add(str(origin_id))
            for destination_id in destinations:
                if destination_id != "__OUT_OF_DISTRICT__":
                    destination_ids.add(str(destination_id))

    for station_prob in inbound.values():
        destination_ids.update(map(str, station_prob.keys()))

    for destination_map in inbound_by_od.values():
        for station_prob in destination_map.values():
            destination_ids.update(map(str, station_prob.keys()))

    return {
        "origin_ids": origin_ids,
        "destination_ids": destination_ids,
        "all_ids": origin_ids | destination_ids,
    }


def collect_transition_districts(transition_dir: Path, profile: str) -> set[str]:
    """蒐集 transition matrix 內出現的行政區。"""

    inter = load_json(transition_dir / f"{profile}_inter_district_transition.json")
    station_exit = load_json(
        transition_dir / f"{profile}_station_exit_transition_by_district.json"
    )
    inbound = load_json(
        transition_dir / f"{profile}_inbound_station_transition_by_district.json"
    )
    districts = set(inter.keys()) | set(station_exit.keys()) | set(inbound.keys())
    for destinations in inter.values():
        districts.update(destinations.keys())
    return set(map(str, districts))


def build_current_compatible_model(
    static_stations: dict[str, StaticStation],
    transition_dir: Path,
    profile: str,
) -> BaselineModel:
    """建立一個目前 baseline 可接受的近似模型。

    這裡使用：
    - static station registry 建立 Station。
    - `inter_district_transition` 建立 DummyNode 的 district-level routing。
    - `inbound_station_transition_by_district` 建立 destination district 的 flat station choice。

    這可以驗證資料型別與基本架構可接入；但尚未使用 station-first exact matrix。
    """

    env = simpy.Environment()
    stations = [
        Station(
            env,
            station.station_id,
            capacity=30,
            initial_bikes=15,
            district_id=station.district,
        )
        for station in static_stations.values()
    ]

    inter = load_json(transition_dir / f"{profile}_inter_district_transition.json")
    inbound = load_json(
        transition_dir / f"{profile}_inbound_station_transition_by_district.json"
    )
    districts = sorted({station.district for station in static_stations.values()})
    station_ids = set(static_stations)
    district_set = set(districts)
    dummy_nodes = [
        DummyNode(
            env,
            district,
            inter_dist_prob={
                destination_district: probability
                for destination_district, probability in inter.get(district, {}).items()
                if destination_district in district_set
            },
            intra_dist_prob={
                station_id: probability
                for station_id, probability in inbound.get(district, {}).items()
                if station_id in station_ids
            },
        )
        for district in districts
    ]

    return BaselineModel(
        env,
        stations,
        dummy_nodes,
        constant_travel_times(
            station_to_dummy_minutes=1.0,
            dummy_to_dummy_minutes=10.0,
            dummy_to_station_minutes=1.0,
            station_to_station_minutes=5.0,
        ),
        rng=random.Random(20260527),
        event_log_enabled=False,
    )


def build_exact_route_model(
    static_stations: dict[str, StaticStation],
    transition_dir: Path,
    profile: str,
) -> BaselineModel:
    """建立採用 station-first route planner 的真實資料模型。"""

    env = simpy.Environment()
    stations = [
        Station(
            env,
            station.station_id,
            capacity=30,
            initial_bikes=15,
            district_id=station.district,
        )
        for station in static_stations.values()
    ]
    districts = sorted({station.district for station in static_stations.values()})
    dummy_nodes = [
        DummyNode(env, district, inter_dist_prob={}, intra_dist_prob={})
        for district in districts
    ]
    route_planner = load_station_exit_route_planner(transition_dir, profile)

    return BaselineModel(
        env,
        stations,
        dummy_nodes,
        constant_travel_times(
            station_to_dummy_minutes=1.0,
            dummy_to_dummy_minutes=10.0,
            dummy_to_station_minutes=1.0,
            station_to_station_minutes=5.0,
        ),
        rng=random.Random(20260527),
        route_planner=route_planner,
        event_log_enabled=False,
    )


def run_small_generator_smoke_test(
    model: BaselineModel,
    rent_lambda_by_station: dict[str, dict[int, float]],
    sample_size: int = 5,
) -> dict[str, Any]:
    """用少量站點啟動 demand_generator，確認 lambda 格式能被採納。"""

    env = model.env
    sample_station_ids = sorted(model.stations)[:sample_size]
    for station_id in sample_station_ids:
        env.process(
            demand_generator(
                env,
                model.stations[station_id],
                rent_lambda_by_station[station_id],
            )
        )
    env.run(until=120.0)
    return {
        "sample_station_ids": sample_station_ids,
        "simulation_minutes": 120.0,
        "event_count": len(model.event_log),
        "status": "passed",
    }


def write_missing_station_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "station_id",
        "rate_period_count",
        "avg_lambda_rent_hr",
        "max_lambda_rent_hr",
        "avg_lambda_return_hr",
        "max_lambda_return_hr",
        "weekday_origin_count",
        "weekday_destination_count",
        "weekend_origin_count",
        "weekend_destination_count",
        "observed_names",
        "observed_districts",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            serialized["observed_names"] = " | ".join(row["observed_names"])
            serialized["observed_districts"] = " | ".join(row["observed_districts"])
            writer.writerow(serialized)


def write_report(path: Path, result: dict[str, Any]) -> None:
    missing_rows = result["missing_rate_station_analysis"]
    excluded_station_ids = result.get("arrival_excluded_station_ids", {})
    lines = [
        "# Real System Input Validation Report",
        "",
        "本報告由 `scripts/validate_real_system_inputs.py` 產生。",
        "",
        "## 1. Rate 有但 Static 沒有的站點與排除紀錄",
        "",
        f"目前輸入中 rate 有但 static 沒有的站點數：{len(missing_rows)}",
        f"已依清理規則排除的站點數：{len(excluded_station_ids)}",
        "",
    ]

    if excluded_station_ids:
        lines.extend(["已排除站點：", ""])
        for station_id, reason in excluded_station_ids.items():
            lines.append(f"- {station_id}：{reason}")
        lines.append("")

    for row in missing_rows:
        lines.extend(
            [
                f"### {row['station_id']}",
                "",
                f"- rate period 數：{row['rate_period_count']}",
                f"- 平均租借 lambda/hr：{row['avg_lambda_rent_hr']:.4f}",
                f"- 最大租借 lambda/hr：{row['max_lambda_rent_hr']:.4f}",
                f"- 平均還車 lambda/hr：{row['avg_lambda_return_hr']:.4f}",
                f"- 最大還車 lambda/hr：{row['max_lambda_return_hr']:.4f}",
                f"- 週間 OD origin count：{row['weekday_origin_count']}",
                f"- 週間 OD destination count：{row['weekday_destination_count']}",
                f"- 週末 OD origin count：{row['weekend_origin_count']}",
                f"- 週末 OD destination count：{row['weekend_destination_count']}",
                f"- OD 中觀察到的站名：{', '.join(row['observed_names']) or '(無)'}",
                f"- OD 中觀察到的行政區：{', '.join(row['observed_districts']) or '(無)'}",
                "",
            ]
        )

    lines.extend(
        [
            "評估：",
            "",
            "- 已排除的站點缺少 static 位置與行政區資料，因此不能建立 `Station`，也不能放入真實座標視覺化。",
            "- 第一版正式模擬會使用清理後資料；若之後能補齊 static 資料，再重新納入。",
            "",
            "## 2. Current Baseline 採納檢查",
            "",
            f"- static station 數：{result['static_station_count']}",
            f"- rent lambda station 數：{result['rent_lambda_station_count']}",
            f"- static 有但 rent lambda 沒有的站點數：{len(result['static_without_rent_lambda'])}",
            f"- current-compatible model 建立狀態：{result['current_compatible_model_status']}",
            f"- current-compatible demand generator smoke test：{result['current_compatible_generator_smoke_test']['status']}",
            f"- exact route model 建立狀態：{result['exact_route_model_status']}",
            f"- exact route demand generator smoke test：{result['exact_route_generator_smoke_test']['status']}",
            f"- smoke test 使用站點：{', '.join(result['exact_route_generator_smoke_test']['sample_station_ids'])}",
            "",
            "可直接採納：",
            "",
            "- `Station` 可由 `youbike_static_info.csv` 建立。",
            "- `demand_generator` 可直接使用 `hourly_rent_lambda_by_station.json`。",
            "- `DummyNode` 可使用 district-level `inter_district_transition` 與 flat `inbound_station_transition_by_district` 建立近似 routing。",
            "",
            "已補上：",
            "",
            "- `BaselineModel` 現在可接受自訂 `route_planner`。",
            "- `StationExitRoutePlanner` 可使用 station-first matrix：起點站先選同區目的站或 `__OUT_OF_DISTRICT__`。",
            "- exact route smoke test 已確認真實 OD matrix 可被核心 Rider 流程採納。",
            "",
            "仍需擴充：",
            "",
            "- 尚未建立完整大規模 scenario runner。",
            "- 尚未接入真實 station capacity 與 initial bikes。",
            "- 若使用未清理 transition matrix，route planner 會排除沒有 static registry 的目的行政區；clean matrix 已先套用 district alias。",
            "",
            "## 3. Transition Matrix 與 Static Station 對齊",
            "",
        ]
    )

    for profile, summary in result["transition_alignment"].items():
        lines.extend(
            [
                f"### {profile}",
                "",
                f"- transition 中 station id 數：{summary['transition_station_id_count']}",
                f"- transition 有但 static 沒有的 station id 數：{summary['transition_ids_not_in_static_count']}",
                f"- static 有但 transition 沒有的 station id 數：{summary['static_ids_not_in_transition_count']}",
                f"- transition 有但 static 沒有的行政區數：{summary['transition_districts_not_in_static_count']}",
                f"- transition 有但 static 沒有的行政區：{', '.join(summary['transition_districts_not_in_static']) or '(無)'}",
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def validate_real_system_inputs(
    project_root: Path,
    output_dir: Path,
    profile: str,
    arrival_dir: Path | None = None,
    transition_dir: Path | None = None,
) -> dict[str, Any]:
    static_csv = project_root / "data/raw/youbike_static_info.csv"
    arrival_csv = project_root / "data/raw/stations_arrival_rates_2hr.csv"
    arrival_dir = arrival_dir or project_root / "data/processed/arrival_rates"
    transition_dir = transition_dir or project_root / "data/processed/transition_matrices"
    geojson_paths = sorted((project_root / "data/raw").glob("*.geojson"))

    static_stations = load_static_stations(static_csv)
    arrival_rows = load_arrival_rate_rows(arrival_csv)
    arrival_metadata = load_json(arrival_dir / "arrival_rate_metadata.json")
    rent_lambda_by_station = load_hourly_lambda_by_station(
        arrival_dir / "hourly_rent_lambda_by_station.json"
    )
    missing_rate_station_ids = arrival_metadata["stations_in_rate_not_in_static"]
    missing_rate_station_analysis = analyze_missing_rate_stations(
        missing_rate_station_ids,
        arrival_rows,
        geojson_paths,
    )

    transition_alignment: dict[str, Any] = {}
    static_ids = set(static_stations)
    for transition_profile in ["weekday", "weekend"]:
        transition_ids = collect_transition_station_ids(
            transition_dir,
            transition_profile,
        )["all_ids"]
        transition_districts = collect_transition_districts(
            transition_dir,
            transition_profile,
        )
        static_districts = {station.district for station in static_stations.values()}
        transition_alignment[transition_profile] = {
            "transition_station_id_count": len(transition_ids),
            "transition_ids_not_in_static": sorted(transition_ids - static_ids),
            "transition_ids_not_in_static_count": len(transition_ids - static_ids),
            "static_ids_not_in_transition": sorted(static_ids - transition_ids),
            "static_ids_not_in_transition_count": len(static_ids - transition_ids),
            "transition_districts_not_in_static": sorted(
                transition_districts - static_districts
            ),
            "transition_districts_not_in_static_count": len(
                transition_districts - static_districts
            ),
        }

    compatible_model = build_current_compatible_model(
        static_stations,
        transition_dir,
        profile,
    )
    compatible_smoke_test = run_small_generator_smoke_test(
        compatible_model,
        rent_lambda_by_station,
    )

    exact_model = build_exact_route_model(static_stations, transition_dir, profile)
    exact_smoke_test = run_small_generator_smoke_test(
        exact_model,
        rent_lambda_by_station,
    )

    result = {
        "static_station_count": len(static_stations),
        "rent_lambda_station_count": len(rent_lambda_by_station),
        "arrival_excluded_station_ids": arrival_metadata.get(
            "excluded_station_ids",
            {},
        ),
        "static_without_rent_lambda": sorted(static_ids - set(rent_lambda_by_station)),
        "missing_rate_station_analysis": missing_rate_station_analysis,
        "current_compatible_model_status": "passed",
        "current_compatible_generator_smoke_test": compatible_smoke_test,
        "exact_route_model_status": "passed",
        "exact_route_generator_smoke_test": exact_smoke_test,
        "transition_alignment": transition_alignment,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_missing_station_csv(
        output_dir / "rate_stations_missing_from_static.csv",
        missing_rate_station_analysis,
    )
    (output_dir / "real_system_input_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_report(output_dir / "real_system_input_validation_report.md", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate real YouBike inputs before building full-system simulation."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="專案根目錄。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/processed/input_validation"),
        help="驗證報告輸出資料夾。",
    )
    parser.add_argument(
        "--profile",
        choices=["weekday", "weekend"],
        default="weekday",
        help="用哪一套 transition matrix 建立 current-compatible smoke model。",
    )
    parser.add_argument(
        "--arrival-dir",
        type=Path,
        default=None,
        help="已處理 arrival rate 資料夾。",
    )
    parser.add_argument(
        "--transition-dir",
        type=Path,
        default=None,
        help="已處理 transition matrix 資料夾。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = validate_real_system_inputs(
        args.project_root,
        args.output_dir,
        args.profile,
        args.arrival_dir,
        args.transition_dir,
    )
    print(
        "real input validation passed: "
        f"static={result['static_station_count']}, "
        f"rent_lambda={result['rent_lambda_station_count']}, "
        f"missing_rate_static={len(result['missing_rate_station_analysis'])}"
    )
    print(f"Validation report written to: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
