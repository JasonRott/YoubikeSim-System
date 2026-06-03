"""最小可執行範例：示範 Phase 1 baseline model 如何組裝。

這不是正式實驗設定，只是用來確認類別之間的資料流與 SimPy process 可以運作。
"""

from __future__ import annotations

import random
from pathlib import Path
import sys

import simpy

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from youbike_sim import (  # noqa: E402
    BaselineModel,
    DummyNode,
    Station,
    constant_travel_times,
    demand_generator,
)


def main() -> None:
    """建立兩個行政區、四個站點，並執行一天的 baseline 模擬。"""

    env = simpy.Environment()
    rng = random.Random(42)

    stations = [
        Station(env, station_id="A1", capacity=10, initial_bikes=6, district_id="A"),
        Station(env, station_id="A2", capacity=10, initial_bikes=4, district_id="A"),
        Station(env, station_id="B1", capacity=10, initial_bikes=5, district_id="B"),
        Station(env, station_id="B2", capacity=10, initial_bikes=5, district_id="B"),
    ]

    dummy_nodes = [
        DummyNode(
            env,
            node_id="A",
            inter_dist_prob={"A": 0.7, "B": 0.3},
            intra_dist_prob={"A1": 0.5, "A2": 0.5},
        ),
        DummyNode(
            env,
            node_id="B",
            inter_dist_prob={"A": 0.4, "B": 0.6},
            intra_dist_prob={"B1": 0.6, "B2": 0.4},
        ),
    ]

    model = BaselineModel(
        env=env,
        stations=stations,
        dummy_nodes=dummy_nodes,
        travel_time_functions=constant_travel_times(
            station_to_dummy_minutes=2.0,
            dummy_to_dummy_minutes=8.0,
            dummy_to_station_minutes=2.0,
            station_to_station_minutes=4.0,
        ),
        rng=rng,
    )

    # lambda 單位為「每小時到達人數」。這裡先用每日 24 小時重複的簡化輸入。
    weekday_lambda = {
        hour: 18.0 if 7 <= hour <= 9 or 17 <= hour <= 19 else 4.0
        for hour in range(24)
    }

    for station in stations:
        env.process(demand_generator(env, station, weekday_lambda))

    env.run(until=24 * 60)

    print("Station snapshots:")
    for snapshot in model.station_snapshots():
        print(snapshot)
    print(f"Total logged events: {len(model.event_log)}")


if __name__ == "__main__":
    main()
