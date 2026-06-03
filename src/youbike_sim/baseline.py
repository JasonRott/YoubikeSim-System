"""Phase 1 baseline model for a YouBike 2.0 discrete-event simulation.

本檔案只實作「自然借還車」的 baseline，不包含調度卡車。
整體模型採用 SimPy 的 next-event time advance，由 env.run(until=...) 控制終止時間。
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import random
from typing import Any, Callable, Iterable, Mapping

import simpy


StationId = str
DistrictId = str
ProbabilityVector = Mapping[Any, float]


def _to_station_id(value: Any) -> StationId:
    """將外部傳入的站點代碼統一轉成字串，避免 int/str 混用造成查找失敗。"""

    return str(value)


def _to_district_id(value: Any) -> DistrictId:
    """將行政區代碼統一轉成字串，方便 dictionary key 管理。"""

    return str(value)


def weighted_choice(probabilities: ProbabilityVector, rng: random.Random) -> Any:
    """依照權重抽樣；權重不必剛好加總為 1，但不能全部小於等於 0。"""

    positive_items = [
        (item, float(weight))
        for item, weight in probabilities.items()
        if float(weight) > 0
    ]
    if not positive_items:
        raise ValueError("Probability vector must contain at least one positive weight.")

    total_weight = sum(weight for _, weight in positive_items)
    threshold = rng.random() * total_weight
    cumulative = 0.0
    for item, weight in positive_items:
        cumulative += weight
        if threshold <= cumulative:
            return item

    # 浮點數誤差的保險：理論上前面的迴圈一定會回傳。
    return positive_items[-1][0]


@dataclass(frozen=True)
class TravelTimeFunctions:
    """集中保存所有旅行時間函式，方便未來替換成 lognormal 或資料驅動模型。

    所有函式都回傳「分鐘」。Phase 1 可先用常數函式；之後只需要替換 callable，
    Rider 流程本身不用改。若某個情境需要依照完整路徑判斷三段時間，可以額外提供
    route_leg_times；例如跨行政區與同行政區使用不同時間結構。
    """

    station_to_dummy: Callable[["Station", "DummyNode"], float]
    dummy_to_dummy: Callable[["DummyNode", "DummyNode"], float]
    dummy_to_station: Callable[["DummyNode", "Station"], float]
    station_to_station: Callable[["Station", "Station"], float]
    route_leg_times: Callable[
        ["Station", "DummyNode", "DummyNode", "Station"],
        tuple[float, float, float],
    ] | None = None


def constant_travel_times(
    station_to_dummy_minutes: float = 3.0,
    dummy_to_dummy_minutes: float = 12.0,
    dummy_to_station_minutes: float = 3.0,
    station_to_station_minutes: float = 5.0,
) -> TravelTimeFunctions:
    """建立一組常數旅行時間，作為 baseline 與教學用途的預設值。"""

    return TravelTimeFunctions(
        station_to_dummy=lambda station, dummy: station_to_dummy_minutes,
        dummy_to_dummy=lambda origin_dummy, destination_dummy: dummy_to_dummy_minutes,
        dummy_to_station=lambda dummy, station: dummy_to_station_minutes,
        station_to_station=lambda origin_station, destination_station: station_to_station_minutes,
    )


def make_distance_based_station_selector(
    distance_func: Callable[[Station, Station], float],
    rng: random.Random | None = None,
    tie_tolerance: float = 1e-9,
) -> Callable[[Station, Iterable[Station]], Station | None]:
    """建立距離型最近站選擇器。

    current_station 是 rider 目前滿站的站點，candidates 是仍有空柱的候選站。
    函式會選出距離最短的站；若多個站距離相同，會用 rng 隨機挑一個，避免固定偏向
    station id 較小的站點。
    """

    selector_rng = rng or random.Random()

    def selector(
        current_station: Station,
        candidates: Iterable[Station],
    ) -> Station | None:
        candidate_list = list(candidates)
        if not candidate_list:
            return None

        scored_candidates: list[tuple[float, Station]] = []
        for station in candidate_list:
            distance = float(distance_func(current_station, station))
            if distance < 0:
                raise ValueError("Station distance must be non-negative.")
            scored_candidates.append((distance, station))

        min_distance = min(distance for distance, _ in scored_candidates)
        nearest_candidates = [
            station
            for distance, station in scored_candidates
            if abs(distance - min_distance) <= tie_tolerance
        ]
        return selector_rng.choice(nearest_candidates)

    return selector


@dataclass(frozen=True)
class RoutePlan:
    """Rider 成功借車後，保存本次旅程的完整路徑規劃結果。"""

    origin_dummy: "DummyNode"
    destination_dummy: "DummyNode"
    destination_station: "Station"


RoutePlanner = Callable[["BaselineModel", "Station"], RoutePlan]


class Station:
    """實體 YouBike 站點。

    bikes 與 docks 都使用 simpy.Container：
    - bikes.level 代表目前可借車數
    - docks.level 代表目前空柱數

    注意：租借失敗不等待，直接記錄 shortage 後離開；還車遇滿站時會先記錄 full，
    後續是否等待與尋找附近站點由 Rider 流程控制。
    """

    def __init__(
        self,
        env: simpy.Environment,
        station_id: Any,
        capacity: int,
        initial_bikes: int,
        district_id: Any,
    ) -> None:
        if capacity <= 0:
            raise ValueError("Station capacity must be positive.")
        if not 0 <= initial_bikes <= capacity:
            raise ValueError("Initial bikes must be between 0 and station capacity.")

        self.env = env
        self.station_id = _to_station_id(station_id)
        self.capacity = int(capacity)
        self.initial_bikes = int(initial_bikes)
        self.district_id = _to_district_id(district_id)

        self.bikes = simpy.Container(env, capacity=self.capacity, init=self.initial_bikes)
        self.docks = simpy.Container(
            env,
            capacity=self.capacity,
            init=self.capacity - self.initial_bikes,
        )

        self.shortage_count = 0
        self.full_count = 0
        self.rental_count = 0
        self.return_count = 0

        # BaselineModel 建立時會回填，讓 Rider(env, origin_station) 可以找到全域網路。
        self.model: BaselineModel | None = None

    @property
    def available_bikes(self) -> int:
        """目前可借車數。"""

        return int(self.bikes.level)

    @property
    def available_docks(self) -> int:
        """目前可停車柱數。"""

        return int(self.docks.level)

    def rent_bike(self, rider_id: str | None = None):
        """嘗試租借一台車；若無車則 balk，並回傳 False。"""

        if self.bikes.level < 1:
            self.shortage_count += 1
            if self.model is not None:
                self.model.log_event(
                    "shortage",
                    rider_id=rider_id,
                    station_id=self.station_id,
                    district_id=self.district_id,
                )
            return False

        yield self.bikes.get(1)
        yield self.docks.put(1)
        self.rental_count += 1
        if self.model is not None:
            self.model.log_event(
                "rental",
                rider_id=rider_id,
                station_id=self.station_id,
                district_id=self.district_id,
                bikes_after=self.available_bikes,
                docks_after=self.available_docks,
            )
        return True

    def return_bike(self, rider_id: str | None = None):
        """嘗試歸還一台車；若滿站則記錄 full，並回傳 False。"""

        if self.docks.level < 1:
            self.full_count += 1
            if self.model is not None:
                self.model.log_event(
                    "full_station",
                    rider_id=rider_id,
                    station_id=self.station_id,
                    district_id=self.district_id,
                )
            return False

        yield self.docks.get(1)
        yield self.bikes.put(1)
        self.return_count += 1
        if self.model is not None:
            self.model.log_event(
                "return",
                rider_id=rider_id,
                station_id=self.station_id,
                district_id=self.district_id,
                bikes_after=self.available_bikes,
                docks_after=self.available_docks,
            )
        return True

    def snapshot(self) -> dict[str, Any]:
        """輸出站點目前狀態，方便做統計或除錯。"""

        return {
            "station_id": self.station_id,
            "district_id": self.district_id,
            "capacity": self.capacity,
            "available_bikes": self.available_bikes,
            "available_docks": self.available_docks,
            "shortage_count": self.shortage_count,
            "full_count": self.full_count,
            "rental_count": self.rental_count,
            "return_count": self.return_count,
        }


class DummyNode:
    """行政區的邏輯 hub，用來避免建立巨大 station-to-station OD matrix。

    inter_dist_prob：
        目前 dummy node 到各目的行政區的機率，例如 {"DaAn": 0.7, "Xinyi": 0.3}

    intra_dist_prob：
        目的行政區內選擇實體站點的機率。支援兩種格式：
        1. flat: {"S1": 0.5, "S2": 0.5}
        2. nested: {"origin_station_id": {"S1": 0.5, "S2": 0.5}}

    nested 格式可以表達你提到的「每個行政區一個 transition matrix」概念；
    flat 格式則適合 Phase 1 先用較簡潔的輸入。
    """

    def __init__(
        self,
        env: simpy.Environment,
        node_id: Any,
        inter_dist_prob: Mapping[Any, float],
        intra_dist_prob: Mapping[Any, Any],
    ) -> None:
        self.env = env
        self.node_id = _to_district_id(node_id)
        self.inter_dist_prob = {
            _to_district_id(district_id): float(probability)
            for district_id, probability in inter_dist_prob.items()
        }
        self.intra_dist_prob = dict(intra_dist_prob)

    def choose_destination_district(self, rng: random.Random) -> DistrictId:
        """由目前 dummy node 抽出目的行政區。"""

        return _to_district_id(weighted_choice(self.inter_dist_prob, rng))

    def choose_destination_station(
        self,
        origin_station_id: Any,
        candidate_stations: Mapping[StationId, Station],
        rng: random.Random,
    ) -> Station:
        """由目的行政區內的候選站點抽出實際還車站。"""

        if not candidate_stations:
            raise ValueError(f"District {self.node_id} has no registered stations.")

        station_prob = self._station_probability_vector(origin_station_id)
        valid_station_prob = {
            _to_station_id(station_id): float(probability)
            for station_id, probability in station_prob.items()
            if _to_station_id(station_id) in candidate_stations
        }

        # 如果沒有提供此行政區的站點機率，先退回均勻抽樣，讓 skeleton 容易跑起來。
        if not valid_station_prob:
            valid_station_prob = {
                station_id: 1.0 for station_id in candidate_stations.keys()
            }

        destination_station_id = _to_station_id(weighted_choice(valid_station_prob, rng))
        return candidate_stations[destination_station_id]

    def _station_probability_vector(self, origin_station_id: Any) -> Mapping[Any, float]:
        """從 flat 或 nested intra_dist_prob 中取出站點機率向量。"""

        normalized_origin_id = _to_station_id(origin_station_id)
        is_nested_matrix = any(
            isinstance(value, Mapping) for value in self.intra_dist_prob.values()
        )

        if not is_nested_matrix:
            return self.intra_dist_prob  # type: ignore[return-value]

        if normalized_origin_id in self.intra_dist_prob:
            nested_value = self.intra_dist_prob[normalized_origin_id]
            if isinstance(nested_value, Mapping):
                return nested_value
            raise ValueError("Nested intra_dist_prob rows must be mappings.")

        if is_nested_matrix:
            return {}

        return {}


class BaselineModel:
    """Phase 1 baseline 的網路管理器。

    這個類別不是額外的模擬實體，而是用來保存全域資料：
    - 所有 Station 與 DummyNode
    - OD 抽樣邏輯
    - 旅行時間函式
    - 事件紀錄
    - 滿站後尋找附近可停車站的策略
    """

    def __init__(
        self,
        env: simpy.Environment,
        stations: Iterable[Station],
        dummy_nodes: Iterable[DummyNode],
        travel_time_functions: TravelTimeFunctions,
        rng: random.Random | None = None,
        return_patience_time: Callable[["Rider", Station], float] | None = None,
        nearest_station_selector: Callable[
            [Station, Iterable[Station]], Station | None
        ]
        | None = None,
        route_planner: RoutePlanner | None = None,
        event_log_enabled: bool = True,
    ) -> None:
        self.env = env
        self.rng = rng or random.Random()
        self.travel_time_functions = travel_time_functions
        self.return_patience_time = return_patience_time or (
            lambda rider, station: self.rng.expovariate(1 / 3.0)
        )
        self.nearest_station_selector = nearest_station_selector
        self.route_planner = route_planner
        self.event_log_enabled = event_log_enabled
        self.event_log: list[dict[str, Any]] = []

        self.stations = {station.station_id: station for station in stations}
        self.dummy_nodes = {dummy.node_id: dummy for dummy in dummy_nodes}
        self.stations_by_district: dict[DistrictId, dict[StationId, Station]] = {}

        for station in self.stations.values():
            station.model = self
            self.stations_by_district.setdefault(station.district_id, {})[
                station.station_id
            ] = station

        self._validate_network()

    def _validate_network(self) -> None:
        """確認每個有站點的行政區都有對應 DummyNode。"""

        missing_dummy_nodes = set(self.stations_by_district) - set(self.dummy_nodes)
        if missing_dummy_nodes:
            raise ValueError(
                "Every station district must have a DummyNode. "
                f"Missing: {sorted(missing_dummy_nodes)}"
            )

    def log_event(self, event_type: str, **payload: Any) -> None:
        """記錄事件；事件時間一律使用 env.now。"""

        if not self.event_log_enabled:
            return
        self.event_log.append(
            {
                "time": float(self.env.now),
                "event_type": event_type,
                **payload,
            }
        )

    def plan_route(self, origin_station: Station) -> RoutePlan:
        """為成功借車的 rider 規劃 DummyNode hub-and-spoke 路徑。"""

        if self.route_planner is not None:
            return self.route_planner(self, origin_station)

        origin_dummy = self.dummy_nodes[origin_station.district_id]
        destination_district_id = origin_dummy.choose_destination_district(self.rng)
        destination_dummy = self.dummy_nodes[destination_district_id]
        candidate_stations = self.stations_by_district[destination_district_id]
        destination_station = destination_dummy.choose_destination_station(
            origin_station.station_id,
            candidate_stations,
            self.rng,
        )
        return RoutePlan(
            origin_dummy=origin_dummy,
            destination_dummy=destination_dummy,
            destination_station=destination_station,
        )

    def sample_return_patience_time(self, rider: "Rider", station: Station) -> float:
        """抽樣滿站後願意等待的時間；預設平均 3 分鐘的 exponential。"""

        patience_time = float(self.return_patience_time(rider, station))
        if patience_time < 0:
            raise ValueError("Return patience time must be non-negative.")
        return patience_time

    def find_nearest_station_with_available_dock(
        self,
        current_station: Station,
    ) -> Station | None:
        """尋找有空柱的附近站點。

        真正的「最近」應由座標、路網或距離矩陣決定；Phase 1 skeleton 先提供
        可替換的 nearest_station_selector。若未提供，預設先找同行政區，再找全系統。
        """

        candidates = [
            station
            for station in self.stations.values()
            if station.station_id != current_station.station_id
            and station.available_docks > 0
        ]
        if self.nearest_station_selector is not None:
            return self.nearest_station_selector(current_station, candidates)

        same_district = [
            station
            for station in candidates
            if station.district_id == current_station.district_id
        ]
        ordered_candidates = same_district or candidates
        if not ordered_candidates:
            return None
        return sorted(ordered_candidates, key=lambda station: station.station_id)[0]

    def get_hourly_arrival_rate(
        self,
        nspp_lambda_dict: Mapping[int, float],
        now: float | None = None,
    ) -> float:
        """取得目前小時的 NSPP lambda。

        假設 lambda 單位是「每小時到達人數」。key 可用：
        - 0..23：代表每日重複的 hour-of-day
        - 0, 1, 2, ...：代表模擬開始後第幾個 absolute hour

        若兩種 key 同時存在，absolute hour 會優先。
        """

        current_time = self.env.now if now is None else now
        absolute_hour = int(current_time // 60)
        hour_of_day = absolute_hour % 24

        if absolute_hour in nspp_lambda_dict:
            return float(nspp_lambda_dict[absolute_hour])
        if hour_of_day in nspp_lambda_dict:
            return float(nspp_lambda_dict[hour_of_day])
        return 0.0

    def station_snapshots(self) -> list[dict[str, Any]]:
        """輸出所有站點狀態。"""

        return [station.snapshot() for station in self.stations.values()]


class Rider:
    """使用者流程：到站、租車、三段式移動、還車，必要時尋找其他站點。"""

    _id_counter = itertools.count(1)

    def __init__(self, env: simpy.Environment, origin_station: Station) -> None:
        if origin_station.model is None:
            raise ValueError("origin_station must be registered in a BaselineModel.")

        self.env = env
        self.origin_station = origin_station
        self.model = origin_station.model
        self.rider_id = f"R{next(self._id_counter)}"

    def run(self):
        """SimPy process 主體。"""

        self.model.log_event(
            "rider_arrival",
            rider_id=self.rider_id,
            station_id=self.origin_station.station_id,
            district_id=self.origin_station.district_id,
        )

        rental_success = yield self.env.process(
            self.origin_station.rent_bike(self.rider_id)
        )
        if not rental_success:
            self.model.log_event(
                "rider_balked",
                rider_id=self.rider_id,
                station_id=self.origin_station.station_id,
            )
            return

        route = self.model.plan_route(self.origin_station)
        (
            station_to_dummy_time,
            dummy_to_dummy_time,
            dummy_to_station_time,
        ) = self._sample_route_leg_times(route)
        self.model.log_event(
            "route_planned",
            rider_id=self.rider_id,
            origin_station_id=self.origin_station.station_id,
            origin_district_id=self.origin_station.district_id,
            destination_district_id=route.destination_dummy.node_id,
            destination_station_id=route.destination_station.station_id,
            station_to_dummy_time=station_to_dummy_time,
            dummy_to_dummy_time=dummy_to_dummy_time,
            dummy_to_station_time=dummy_to_station_time,
            total_travel_time=(
                station_to_dummy_time + dummy_to_dummy_time + dummy_to_station_time
            ),
        )

        yield self.env.timeout(station_to_dummy_time)
        yield self.env.timeout(dummy_to_dummy_time)
        yield self.env.timeout(dummy_to_station_time)

        yield from self._return_until_success(route.destination_station)

    def _sample_route_leg_times(self, route: RoutePlan) -> tuple[float, float, float]:
        """取得本次旅程三段旅行時間，並確認時間不可為負。"""

        if self.model.travel_time_functions.route_leg_times is not None:
            leg_times = self.model.travel_time_functions.route_leg_times(
                self.origin_station,
                route.origin_dummy,
                route.destination_dummy,
                route.destination_station,
            )
        else:
            leg_times = (
                self.model.travel_time_functions.station_to_dummy(
                    self.origin_station,
                    route.origin_dummy,
                ),
                self.model.travel_time_functions.dummy_to_dummy(
                    route.origin_dummy,
                    route.destination_dummy,
                ),
                self.model.travel_time_functions.dummy_to_station(
                    route.destination_dummy,
                    route.destination_station,
                ),
            )

        if len(leg_times) != 3:
            raise ValueError("route_leg_times must return exactly three leg times.")

        normalized_leg_times = tuple(float(leg_time) for leg_time in leg_times)
        if any(leg_time < 0 for leg_time in normalized_leg_times):
            raise ValueError("Travel times must be non-negative.")
        return normalized_leg_times  # type: ignore[return-value]

    def _return_until_success(self, first_station: Station):
        """滿站時等待一段時間；若仍滿站，就前往附近有空柱站點直到成功還車。"""

        current_station = first_station
        while True:
            return_success = yield self.env.process(
                current_station.return_bike(self.rider_id)
            )
            if return_success:
                self.model.log_event(
                    "rider_finished",
                    rider_id=self.rider_id,
                    station_id=current_station.station_id,
                )
                return

            patience_time = self.model.sample_return_patience_time(
                self,
                current_station,
            )
            self.model.log_event(
                "return_wait_started",
                rider_id=self.rider_id,
                station_id=current_station.station_id,
                patience_time=patience_time,
            )
            yield self.env.timeout(patience_time)

            # 等待後先重試原站；若仍滿站，再把找鄰近站視為第二段獨立旅程。
            retry_success = yield self.env.process(
                current_station.return_bike(self.rider_id)
            )
            if retry_success:
                self.model.log_event(
                    "rider_finished_after_wait",
                    rider_id=self.rider_id,
                    station_id=current_station.station_id,
                )
                return

            next_station = self.model.find_nearest_station_with_available_dock(
                current_station
            )
            if next_station is None:
                # 理論上只要系統總容量合理，至少會有一個空柱；這裡保留防呆。
                self.model.log_event(
                    "no_available_dock_in_system",
                    rider_id=self.rider_id,
                    station_id=current_station.station_id,
                )
                continue

            search_time = self.model.travel_time_functions.station_to_station(
                current_station,
                next_station,
            )
            self.model.log_event(
                "search_nearby_station",
                rider_id=self.rider_id,
                from_station_id=current_station.station_id,
                to_station_id=next_station.station_id,
                travel_time=search_time,
            )
            yield self.env.timeout(search_time)
            current_station = next_station


def demand_generator(
    env: simpy.Environment,
    station: Station,
    nspp_lambda_dict: Mapping[int, float],
):
    """依照分段常數 NSPP 產生 rider。

    nspp_lambda_dict 的 lambda 單位是「每小時到達率」。例如 lambda=12 代表平均
    每小時 12 人，即平均每 5 分鐘一人。此 generator 會在小時邊界重新讀取 lambda。
    """

    if station.model is None:
        raise ValueError("station must be registered in a BaselineModel.")

    model = station.model
    while True:
        current_lambda_per_hour = model.get_hourly_arrival_rate(nspp_lambda_dict)
        minutes_until_next_hour = 60.0 - (env.now % 60.0)

        if current_lambda_per_hour <= 0:
            yield env.timeout(minutes_until_next_hour)
            continue

        lambda_per_minute = current_lambda_per_hour / 60.0
        interarrival_time = model.rng.expovariate(lambda_per_minute)

        # 若下一次到達跨過小時邊界，先走到邊界並重新依照新的 lambda 抽樣。
        if interarrival_time >= minutes_until_next_hour:
            yield env.timeout(minutes_until_next_hour)
            continue

        yield env.timeout(interarrival_time)
        rider = Rider(env, station)
        env.process(rider.run())
