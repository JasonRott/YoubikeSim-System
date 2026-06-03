"""兩行政區、六站點的簡單情境測試。

此腳本的目的不是做正式政策實驗，而是產生一個容易人工檢查的情境：
- A、B 兩個行政區，各有 1、2、3 三個站點。
- 同行政區目的地機率合計 2/3，平均分配到另外兩站。
- 跨行政區目的地機率 1/3；因只有兩區，所以只能去另一區，目的站平均分配。
- 每站 arrival rate = 3 人/hr，demand_generator 會換算成每分鐘到達率。
- 同行政區總旅行時間 10 分鐘；跨行政區總旅行時間 24 分鐘。
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import random
import sys
from typing import Any

import simpy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from youbike_sim import (  # noqa: E402
    BaselineModel,
    DummyNode,
    Station,
    TravelTimeFunctions,
    demand_generator,
    make_distance_based_station_selector,
)


def build_simple_model(
    env: simpy.Environment,
    rng: random.Random,
    station_capacity: int = 1000,
    initial_bikes: int = 500,
) -> tuple[BaselineModel, list[Station]]:
    """建立完全對稱的 A、B 兩行政區測試模型。"""

    stations = [
        Station(env, f"{district}{number}", station_capacity, initial_bikes, district)
        for district in ("A", "B")
        for number in (1, 2, 3)
    ]

    same_district_rows = {
        "A1": {"A2": 0.5, "A3": 0.5},
        "A2": {"A1": 0.5, "A3": 0.5},
        "A3": {"A1": 0.5, "A2": 0.5},
        "B1": {"B2": 0.5, "B3": 0.5},
        "B2": {"B1": 0.5, "B3": 0.5},
        "B3": {"B1": 0.5, "B2": 0.5},
    }

    dummy_nodes = [
        DummyNode(
            env,
            node_id="A",
            inter_dist_prob={"A": 2 / 3, "B": 1 / 3},
            intra_dist_prob={
                station_id: row
                for station_id, row in same_district_rows.items()
                if station_id.startswith("A")
            },
        ),
        DummyNode(
            env,
            node_id="B",
            inter_dist_prob={"A": 1 / 3, "B": 2 / 3},
            intra_dist_prob={
                station_id: row
                for station_id, row in same_district_rows.items()
                if station_id.startswith("B")
            },
        ),
    ]

    def route_leg_times(
        origin_station: Station,
        origin_dummy: DummyNode,
        destination_dummy: DummyNode,
        destination_station: Station,
    ) -> tuple[float, float, float]:
        """依照情境設定回傳三段路程時間。

        同行政區：站點 -> dummy 5 分鐘，dummy -> dummy 0 分鐘，dummy -> 站點 5 分鐘。
        跨行政區：站點 -> 區外 12 分鐘，行政區間抽象段 0 分鐘，區外 -> 站點 12 分鐘。
        """

        if origin_station.district_id == destination_station.district_id:
            return 5.0, 0.0, 5.0
        return 12.0, 0.0, 12.0

    travel_times = TravelTimeFunctions(
        station_to_dummy=lambda station, dummy: 0.0,
        dummy_to_dummy=lambda origin_dummy, destination_dummy: 0.0,
        dummy_to_station=lambda dummy, station: 0.0,
        station_to_station=lambda origin_station, destination_station: 10.0,
        route_leg_times=route_leg_times,
    )

    model = BaselineModel(
        env=env,
        stations=stations,
        dummy_nodes=dummy_nodes,
        travel_time_functions=travel_times,
        rng=rng,
        nearest_station_selector=make_distance_based_station_selector(
            distance_func=lambda origin_station, candidate_station: 1.0,
            rng=rng,
        ),
    )
    return model, stations


def summarize_events(
    model: BaselineModel,
    simulation_minutes: int,
    station_capacity: int,
    initial_bikes: int,
) -> dict[str, Any]:
    """把 event_log 壓縮成容易檢查的摘要。"""

    arrivals = [event for event in model.event_log if event["event_type"] == "rider_arrival"]
    routes = [event for event in model.event_log if event["event_type"] == "route_planned"]
    same_district_routes = [
        event
        for event in routes
        if event["origin_district_id"] == event["destination_district_id"]
    ]
    cross_district_routes = [
        event
        for event in routes
        if event["origin_district_id"] != event["destination_district_id"]
    ]
    shortages = [event for event in model.event_log if event["event_type"] == "shortage"]
    full_stations = [
        event for event in model.event_log if event["event_type"] == "full_station"
    ]

    route_time_counts: dict[str, int] = {}
    od_counts: dict[str, int] = {}
    for event in routes:
        route_time = str(event["total_travel_time"])
        route_time_counts[route_time] = route_time_counts.get(route_time, 0) + 1

        od_key = f"{event['origin_station_id']}->{event['destination_station_id']}"
        od_counts[od_key] = od_counts.get(od_key, 0) + 1

    expected_arrivals = 6 * 3 * (simulation_minutes / 60)
    return {
        "simulation_minutes": simulation_minutes,
        "simulation_hours": simulation_minutes / 60,
        "station_capacity": station_capacity,
        "initial_bikes": initial_bikes,
        "arrival_rate_per_station_per_hour": 3.0,
        "expected_total_arrivals": expected_arrivals,
        "observed_total_arrivals": len(arrivals),
        "successful_routes": len(routes),
        "same_district_routes": len(same_district_routes),
        "cross_district_routes": len(cross_district_routes),
        "observed_same_district_ratio": (
            len(same_district_routes) / len(routes) if routes else 0.0
        ),
        "observed_cross_district_ratio": (
            len(cross_district_routes) / len(routes) if routes else 0.0
        ),
        "expected_same_district_ratio": 2 / 3,
        "expected_cross_district_ratio": 1 / 3,
        "shortage_events": len(shortages),
        "full_station_events": len(full_stations),
        "route_time_counts": route_time_counts,
        "od_counts": dict(sorted(od_counts.items())),
        "station_snapshots": model.station_snapshots(),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """寫出 CSV；欄位會合併所有 row 的 key，避免事件 payload 欄位不一致。"""

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = sorted({field for row in rows for field in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown_report(report_dir: Path, summary: dict[str, Any]) -> None:
    """輸出給人看的簡短測試報告。"""

    lines = [
        "# Simple Scenario Report",
        "",
        "## 情境設定",
        "",
        "- 行政區：A、B。",
        "- 每區站點：1、2、3，共 6 站。",
        "- 每站 arrival rate：3 人/hr，模擬內部換算為 0.05 人/min。",
        "- 同行政區目的地機率：2/3，平均分配到另外兩站。",
        "- 跨行政區目的地機率：1/3，目的區只有另一區，目的站平均分配。",
        "- 同行政區總旅行時間：10 分鐘。",
        "- 跨行政區總旅行時間：24 分鐘。",
        f"- 測試用容量：每站 {summary['station_capacity']} 格，初始 {summary['initial_bikes']} 台。",
        "",
        "## 結果摘要",
        "",
        f"- 模擬時間：{summary['simulation_minutes']} 分鐘。",
        f"- 預期總到達人數：約 {summary['expected_total_arrivals']:.1f}。",
        f"- 實際總到達人數：{summary['observed_total_arrivals']}。",
        f"- 成功規劃路線數：{summary['successful_routes']}。",
        f"- 同行政區比例：{summary['observed_same_district_ratio']:.4f}，理論值 0.6667。",
        f"- 跨行政區比例：{summary['observed_cross_district_ratio']:.4f}，理論值 0.3333。",
        f"- shortage 事件數：{summary['shortage_events']}。",
        f"- full-station 事件數：{summary['full_station_events']}。",
        "",
        "## 旅行時間分布",
        "",
    ]
    for route_time, count in sorted(
        summary["route_time_counts"].items(),
        key=lambda item: float(item[0]),
    ):
        lines.append(f"- {route_time} 分鐘：{count} 次")

    report_dir.joinpath("report.md").write_text("\n".join(lines), encoding="utf-8")


def run_simple_scenario(
    days: float,
    seed: int,
    output_root: Path,
    station_capacity: int,
    initial_bikes: int,
) -> Path:
    """執行情境並建立獨立報告資料夾。"""

    simulation_minutes = int(days * 24 * 60)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = output_root / f"simple_scenario_{timestamp}_seed{seed}"
    report_dir.mkdir(parents=True, exist_ok=False)

    env = simpy.Environment()
    rng = random.Random(seed)
    model, stations = build_simple_model(
        env,
        rng,
        station_capacity=station_capacity,
        initial_bikes=initial_bikes,
    )
    hourly_lambda = {hour: 3.0 for hour in range(24)}

    for station in stations:
        env.process(demand_generator(env, station, hourly_lambda))

    env.run(until=simulation_minutes)

    summary = summarize_events(
        model,
        simulation_minutes,
        station_capacity,
        initial_bikes,
    )
    report_dir.joinpath("summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(report_dir / "events.csv", model.event_log)
    write_csv(report_dir / "station_snapshots.csv", model.station_snapshots())
    write_markdown_report(report_dir, summary)
    return report_dir


def parse_args() -> argparse.Namespace:
    """解析命令列參數。"""

    parser = argparse.ArgumentParser(description="Run the simple YouBike scenario.")
    parser.add_argument("--days", type=float, default=7.0, help="simulation days")
    parser.add_argument("--seed", type=int, default=20260524, help="random seed")
    parser.add_argument(
        "--station-capacity",
        type=int,
        default=1000,
        help="capacity of each test station",
    )
    parser.add_argument(
        "--initial-bikes",
        type=int,
        default=500,
        help="initial bikes at each test station",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "report",
        help="root folder for test reports",
    )
    return parser.parse_args()


def main() -> None:
    """命令列進入點。"""

    args = parse_args()
    report_dir = run_simple_scenario(
        args.days,
        args.seed,
        args.output_root,
        args.station_capacity,
        args.initial_bikes,
    )
    print(f"Report written to: {report_dir}")


if __name__ == "__main__":
    main()
