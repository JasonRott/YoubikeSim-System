"""真實 OD transition matrix 的 route planner。

Phase 1 baseline 原本的 `BaselineModel.plan_route()` 採用 district-first：
先抽目的行政區，再抽目的站點。

真實 OD 轉換後，我們更想使用 station-first：
起點站先抽「同行政區目的站」或「區外 Dummy Node」；
若抽到區外，再由 Dummy Node 抽目的行政區與目的站點。

這個模組提供可插入 `BaselineModel(route_planner=...)` 的 planner，
讓核心 Rider 流程不需要大改，也能採納真實資料結構。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .baseline import BaselineModel, RoutePlan, Station, weighted_choice


OUT_OF_DISTRICT = "__OUT_OF_DISTRICT__"


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class StationExitRoutePlanner:
    """依照 station-first transition matrix 規劃 rider 路線。"""

    def __init__(
        self,
        station_exit_transition_by_district: Mapping[str, Mapping[str, Mapping[str, float]]],
        inter_district_transition: Mapping[str, Mapping[str, float]],
        inbound_station_transition_by_district: Mapping[str, Mapping[str, float]],
        inbound_station_transition_by_od_district: Mapping[
            str,
            Mapping[str, Mapping[str, float]],
        ]
        | None = None,
        out_of_district_key: str = OUT_OF_DISTRICT,
        self_station_ratio: float = 0.0,
    ) -> None:
        # 同站來回（起點=終點）的機率；Phase 2 抽出的同站交易在此重新接回模擬。
        self.self_station_ratio = self_station_ratio
        self.station_exit_transition_by_district = station_exit_transition_by_district
        self.inter_district_transition = inter_district_transition
        self.inbound_station_transition_by_district = (
            inbound_station_transition_by_district
        )
        self.inbound_station_transition_by_od_district = (
            inbound_station_transition_by_od_district or {}
        )
        self.out_of_district_key = out_of_district_key
        # 預先彙整每個行政區「典型」的出站分布：把該區所有有 OD row 的站點機率列平均，
        # 給沒有 OD row 的站點當 fallback（比均勻分布真實得多，含該區實際的區外比例與熱門目的站）。
        self.district_pooled_exit = self._build_district_pooled_exit(
            station_exit_transition_by_district
        )

    @staticmethod
    def _build_district_pooled_exit(
        station_exit_transition_by_district: Mapping[str, Mapping[str, Mapping[str, float]]],
    ) -> dict[str, dict[str, float]]:
        pooled_by_district: dict[str, dict[str, float]] = {}
        for district, rows in station_exit_transition_by_district.items():
            pooled: dict[str, float] = defaultdict(float)
            station_count = 0
            for row in rows.values():
                if not isinstance(row, Mapping):
                    continue
                station_count += 1
                for destination, probability in row.items():
                    pooled[destination] += float(probability)
            if station_count > 0:
                pooled_by_district[district] = {
                    destination: mass / station_count
                    for destination, mass in pooled.items()
                }
        return pooled_by_district

    def __call__(self, model: BaselineModel, origin_station: Station) -> RoutePlan:
        """產生符合 Dummy Node 架構的 RoutePlan。"""

        origin_district = origin_station.district_id
        origin_dummy = model.dummy_nodes[origin_district]

        # 同站來回（休閒/購物繞圈）：直接以原站為目的站，占用時間由 travel time 端依「起=終」分流。
        if self.self_station_ratio > 0.0 and model.rng.random() < self.self_station_ratio:
            return RoutePlan(
                origin_dummy=origin_dummy,
                destination_dummy=origin_dummy,
                destination_station=origin_station,
            )

        exit_choice = self._choose_station_exit(model, origin_station)

        if exit_choice != self.out_of_district_key:
            destination_station = model.stations[str(exit_choice)]
            return RoutePlan(
                origin_dummy=origin_dummy,
                destination_dummy=model.dummy_nodes[destination_station.district_id],
                destination_station=destination_station,
            )

        destination_district = self._choose_destination_district(model, origin_district)
        destination_station = self._choose_inbound_station(
            model,
            origin_district,
            destination_district,
        )
        return RoutePlan(
            origin_dummy=origin_dummy,
            destination_dummy=model.dummy_nodes[destination_district],
            destination_station=destination_station,
        )

    def _choose_station_exit(
        self,
        model: BaselineModel,
        origin_station: Station,
    ) -> str:
        """抽同區目的站或區外 Dummy Node。

        若某個起點站沒有 OD row（arrival rate 有需求、但 OD 資料缺該站歷史目的地，
        多半因為 λ 與 OD 來自不同期間/記錄），改用「該行政區典型出站分布」當 fallback；
        若連典型分布都沒有，最後才退回同區均勻，避免模擬中斷。
        """

        district_rows = self.station_exit_transition_by_district.get(
            origin_station.district_id,
            {},
        )
        raw_row = district_rows.get(origin_station.station_id, {})
        valid_row = {
            station_id: probability
            for station_id, probability in raw_row.items()
            if station_id == self.out_of_district_key or station_id in model.stations
        }

        if valid_row:
            return str(weighted_choice(valid_row, model.rng))

        # Fallback 1：用該行政區「典型出站分布」（含真實的區外比例與熱門目的站），排除自己。
        pooled_row = self.district_pooled_exit.get(origin_station.district_id, {})
        valid_pooled = {
            destination: probability
            for destination, probability in pooled_row.items()
            if destination != origin_station.station_id
            and (destination == self.out_of_district_key or destination in model.stations)
        }
        if valid_pooled:
            model.log_event(
                "route_planner_fallback",
                station_id=origin_station.station_id,
                reason="missing_station_exit_row_used_district_pooled",
            )
            return str(weighted_choice(valid_pooled, model.rng))

        # Fallback 2（最後手段）：同區均勻。
        same_district_candidates = {
            station.station_id: 1.0
            for station in model.stations_by_district[origin_station.district_id].values()
            if station.station_id != origin_station.station_id
        }
        if same_district_candidates:
            model.log_event(
                "route_planner_fallback",
                station_id=origin_station.station_id,
                reason="missing_station_exit_row_uniform",
            )
            return str(weighted_choice(same_district_candidates, model.rng))

        return origin_station.station_id

    def _choose_destination_district(
        self,
        model: BaselineModel,
        origin_district: str,
    ) -> str:
        """抽跨行政區目的地；會排除目前模型沒有 station registry 的行政區。"""

        raw_row = self.inter_district_transition.get(origin_district, {})
        valid_row = {
            district: probability
            for district, probability in raw_row.items()
            if district in model.stations_by_district and district in model.dummy_nodes
        }
        if valid_row:
            return str(weighted_choice(valid_row, model.rng))

        # 若跨區 row 全部無效，退回 origin district，讓 rider 仍能完成旅程。
        model.log_event(
            "route_planner_fallback",
            district_id=origin_district,
            reason="missing_valid_inter_district_row",
        )
        return origin_district

    def _choose_inbound_station(
        self,
        model: BaselineModel,
        origin_district: str,
        destination_district: str,
    ) -> Station:
        """抽目的行政區內的站點。"""

        candidate_stations = model.stations_by_district[destination_district]
        od_specific_row = (
            self.inbound_station_transition_by_od_district.get(origin_district, {})
            .get(destination_district, {})
        )
        district_row = self.inbound_station_transition_by_district.get(
            destination_district,
            {},
        )
        raw_row = od_specific_row or district_row
        valid_row = {
            station_id: probability
            for station_id, probability in raw_row.items()
            if station_id in candidate_stations
        }
        if valid_row:
            return candidate_stations[str(weighted_choice(valid_row, model.rng))]

        model.log_event(
            "route_planner_fallback",
            district_id=destination_district,
            reason="missing_valid_inbound_station_row",
        )
        fallback_row = {station_id: 1.0 for station_id in candidate_stations}
        return candidate_stations[str(weighted_choice(fallback_row, model.rng))]


def load_station_exit_route_planner(
    transition_dir: str | Path,
    profile: str,
    self_station_ratio: float = 0.0,
) -> StationExitRoutePlanner:
    """從 `data/processed/transition_matrices` 載入 route planner。"""

    transition_path = Path(transition_dir)
    return StationExitRoutePlanner(
        station_exit_transition_by_district=_load_json(
            transition_path / f"{profile}_station_exit_transition_by_district.json"
        ),
        inter_district_transition=_load_json(
            transition_path / f"{profile}_inter_district_transition.json"
        ),
        inbound_station_transition_by_district=_load_json(
            transition_path / f"{profile}_inbound_station_transition_by_district.json"
        ),
        inbound_station_transition_by_od_district=_load_json(
            transition_path
            / f"{profile}_inbound_station_transition_by_od_district.json"
        ),
        self_station_ratio=self_station_ratio,
    )
