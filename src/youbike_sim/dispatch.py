"""調度（rebalancing）模組：集散場 Depot、調度卡車 DispatchTruck，與調度政策。

設計依據（見 system_upgrade/architecture_narrative.md §16）：
- 貼真實 12 行政區規模、每區多台調度車（車數參數化，最後做敏感度）。
- 高度參考組員 Dispatching Policies：站點門檻 LCL/UCL、目標值（補至 70% / 抽至 30%）、
  卡車容量 30、起始載 15、15km/h、handling = 3 + 0.5×台數、集散場緩衝庫存。
- 站點分型採「現況分型」：到站掃描時，低於 LCL → 補車至 high target（易少行為）、
  高於 UCL → 抽車至 low target（易多行為）。這同時也是提案 p.12 Dynamic Threshold 的字面邏輯，
  並避開靜態 易多/易少 在時段間翻轉的失真（見 §16.5）。

卡車對站點的車輛搬移直接操作 Station 的 simpy.Container：
- 補車（站點 +q）：station.docks.get(q) → station.bikes.put(q)，卡車 load -= q。
- 抽車（站點 −q）：station.bikes.get(q) → station.docks.put(q)，卡車 load += q。
搬移量在「到站當下」依當前 level 重新計算且不跨 yield，故 get/put 不會阻塞。

本模組不修改 baseline.py；卡車事件透過 model.log_event 寫入同一份 event_log。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import math
import random
from typing import Any, Mapping

import simpy

from .baseline import BaselineModel, Station


# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #


@dataclass
class DispatchConfig:
    """調度共用參數。比率以站點容量為基準。"""

    # 健康帶與目標
    lcl_ratio: float = 0.20            # 缺車警戒線
    ucl_ratio: float = 0.80            # 滿站警戒線
    target_low_ratio: float = 0.30     # 抽車目標（高於 UCL → 抽到此，= LCL+10%）
    target_high_ratio: float = 0.70    # 補車目標（低於 LCL → 補到此，= UCL-10%）

    # 卡車（汽車，車速較快；每段旅程對車速做高斯抽樣）
    truck_capacity: int = 30
    truck_initial_load: int = 15
    truck_speed_kmph: float = 50.0          # 車速高斯均值
    truck_speed_sd_kmph: float = 10.0       # 車速高斯標準差
    truck_speed_min_kmph: float = 10.0      # 車速截斷下限（避免抽到過小/負值）
    handling_setup_min: float = 3.0
    handling_per_bike_min: float = 0.5

    # Policy 1 選站 score 的距離常數：Score = |current-target| / (distance + 此值)
    score_distance_const_km: float = 0.1

    # 集散場（每區一座，有限緩衝庫存）
    depot_capacity: int = 300
    depot_initial: int = 300

    # 成本係數（DispatchingCost = C_labor·工時 + C_km·里程 + C_trip·出車次數 + C_fix·車隊）
    # 預設採台灣可辯護值（NT$）；見 architecture_narrative §18。
    cost_per_labor_hour: float = 300.0  # C_labor：人力每工時（司機 ~53.5k/月）
    cost_per_km: float = 8.0            # C_mileage：每公里（油+車輛耗損）
    cost_per_trip: float = 50.0         # C_trip：每次出車雜支（人力已另計，不放大額）
    cost_per_truck_fixed: float = 0.0   # C_fix：每車隊固定/攤提（預設 0；同車隊相消，敏感度才開）

    # P5 需求加權選站：score 乘上 (站點日λ)^alpha。0=關（無加權），0.5=√λ（溫和，使用者選）。
    demand_weight_alpha: float = 0.0

    # 行動量門檻（比率）：站點「偏離目標 / 容量」≥ 此比率才算「值得服務」的候選。
    # 用比率而非絕對台數，避免小容量站被不公平過濾（小站每台車造成的比率變化更大）。
    # 註：破 LCL/UCL 的站其偏離/容量必 ≥0.5，故 ≤0.5 的門檻等於「所有破門檻站皆值得去」（含小站）。
    min_action_ratio: float = 0.15

    # 人力（與車綁定）：值勤窗、強制休息、出車冷卻
    duty_start_minute: float = 360.0     # 值勤窗起（06:00）；窗外下班、不出車不計薪
    duty_end_minute: float = 1440.0      # 值勤窗迄（24:00）
    # 可選：多段值勤窗（部分時段待命，例如只在尖峰前後）。None → 用上面單一窗 [start,end]。
    duty_windows: tuple[tuple[float, float], ...] | None = None
    max_continuous_work_minutes: float = 240.0  # 連續工作上限（4hr）→ 強制休息
    rest_minutes: float = 45.0           # 強制休息時長（不計薪）
    cooldown_minutes: float = 10.0       # 每次返場後冷卻（待命，計薪）才能再出車

    # Policy 1 固定巡迴：每日出車起點（分鐘）與每班時長（分鐘）
    patrol_start_minutes: tuple[float, ...] = (360.0, 960.0)  # 06:00、16:00
    patrol_duration_minutes: float = 90.0                      # 1.5 小時

    # Policy 2 動態觸發
    dynamic_scan_minutes: float = 5.0              # 每 5 分鐘掃描（對齊 5 分網格，使全區同步）
    dynamic_warning_count: int = 3                 # 區內破門檻的站數 ≥ 此值即觸發
    score_distance_const_km_dynamic: float = 0.5   # Policy 2/反應層 選站 score 的距離常數
    # Policy 2 選站改用平方放大極端：Score = (current-target)^2 / (distance + 上者)

    # 反應層（P3）警戒帶：比健康帶 20/80 緊、比危急 0/100 早（使用者洞見，見 §18.3）
    alert_low_ratio: float = 0.10
    alert_high_ratio: float = 0.90

    # P6 預測式預置（hybrid_forecast）：見 policy_rules.md D 節
    preposition_minutes: tuple[float, ...] = (330.0, 930.0)  # 05:30、15:30（各尖峰前）
    forecast_horizon_hours: float = 3.0     # 預測未來幾小時的借走量
    forecast_base_ratio: float = 0.65       # 無預測流出時的基準水位（使用者選 60-70%）


# --------------------------------------------------------------------------- #
# 距離
# --------------------------------------------------------------------------- #


def km_between(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """以經緯度（WGS84）近似平面距離（公里）。與 scenario.euclidean_km 同公式。"""

    mean_lat = math.radians((a["latitude"] + b["latitude"]) / 2.0)
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(mean_lat)
    dx = (b["longitude"] - a["longitude"]) * m_per_deg_lon
    dy = (b["latitude"] - a["latitude"]) * m_per_deg_lat
    return math.hypot(dx, dy) / 1000.0


def weiszfeld(
    pts: list[tuple[float, float]],
    max_iter: int = 200,
    tol: float = 1e-7,
) -> tuple[float, float]:
    """平面點集的幾何中位數（Weber point），用 Weiszfeld 迭代。

    幾何中位數最小化「到所有點的距離總和」（算術平均只最小化距離平方和）。
    """

    if not pts:
        raise ValueError("pts must be non-empty.")
    if len(pts) == 1:
        return pts[0]
    x = sum(p[0] for p in pts) / len(pts)
    y = sum(p[1] for p in pts) / len(pts)
    for _ in range(max_iter):
        num_x = num_y = denom = 0.0
        for px, py in pts:
            d = math.hypot(px - x, py - y)
            if d < 1e-9:
                continue
            w = 1.0 / d
            num_x += px * w
            num_y += py * w
            denom += w
        if denom == 0.0:
            break
        nx, ny = num_x / denom, num_y / denom
        if math.hypot(nx - x, ny - y) < tol:
            x, y = nx, ny
            break
        x, y = nx, ny
    return x, y


def geometric_median(
    coords: list[tuple[float, float]],
    max_iter: int = 200,
    tol: float = 1e-7,
) -> tuple[float, float]:
    """一組 (latitude, longitude) 的幾何中位數，回傳 (lat, lon)。

    以局部等距投影（公尺）跑 Weiszfeld 後轉回經緯度，使距離為實際公尺尺度。
    """

    if not coords:
        raise ValueError("coords must be non-empty.")
    if len(coords) == 1:
        return coords[0]
    lat0 = sum(c[0] for c in coords) / len(coords)
    lon0 = sum(c[1] for c in coords) / len(coords)
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    pts = [
        ((lon - lon0) * m_per_deg_lon, (lat - lat0) * m_per_deg_lat)
        for lat, lon in coords
    ]
    x, y = weiszfeld(pts, max_iter, tol)
    return lat0 + y / m_per_deg_lat, lon0 + x / m_per_deg_lon


# --------------------------------------------------------------------------- #
# 集散場
# --------------------------------------------------------------------------- #


class Depot:
    """行政區集散場：固定於行政區中心，持有有限緩衝庫存供卡車取/卸。"""

    def __init__(
        self,
        district_id: str,
        latitude: float,
        longitude: float,
        capacity: int,
        initial: int,
    ) -> None:
        self.district_id = str(district_id)
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.capacity = int(capacity)
        self.inventory = int(initial)

    @property
    def position(self) -> dict[str, float]:
        return {"latitude": self.latitude, "longitude": self.longitude}

    def take(self, qty: int) -> int:
        """卡車從集散場取車；回傳實際取得（受庫存限制）。"""

        qty = min(int(qty), self.inventory)
        if qty <= 0:
            return 0
        self.inventory -= qty
        return qty

    def give(self, qty: int) -> int:
        """卡車把車卸回集散場；回傳實際接收（受容量限制）。"""

        qty = min(int(qty), self.capacity - self.inventory)
        if qty <= 0:
            return 0
        self.inventory += qty
        return qty


# --------------------------------------------------------------------------- #
# 成本統計
# --------------------------------------------------------------------------- #


@dataclass
class DispatchStats:
    """調度成本與工作量統計，供 SC Ratio 與報告使用。"""

    trips: int = 0                 # 出車次數（dispatch frequency）
    total_km: float = 0.0          # 總里程
    total_active_minutes: float = 0.0  # 卡車實際在動時間（移動+搬運）
    on_duty_minutes: float = 0.0   # 值勤含待命的計薪工時（不含休息/下班）
    rest_minutes: float = 0.0      # 休息時間（不計薪）
    bikes_replenished: int = 0     # 補出的車總數
    bikes_withdrawn: int = 0       # 抽走的車總數
    station_visits: int = 0        # 有效搬運的到站次數
    wasted_visits: int = 0         # 白跑（到站已達標，無搬運）

    def as_dict(self) -> dict[str, Any]:
        return {
            "trips": self.trips,
            "total_km": round(self.total_km, 3),
            "total_active_minutes": round(self.total_active_minutes, 2),
            "on_duty_minutes": round(self.on_duty_minutes, 2),
            "on_duty_hours": round(self.on_duty_minutes / 60.0, 2),
            "rest_minutes": round(self.rest_minutes, 2),
            "bikes_replenished": self.bikes_replenished,
            "bikes_withdrawn": self.bikes_withdrawn,
            "bikes_moved": self.bikes_replenished + self.bikes_withdrawn,
            "station_visits": self.station_visits,
            "wasted_visits": self.wasted_visits,
        }


# --------------------------------------------------------------------------- #
# 調度系統（集中持有 depots / trucks / config / stats，並負責分型與選站）
# --------------------------------------------------------------------------- #


class DispatchSystem:
    """調度系統管理器：建立每區集散場與卡車、提供分型/選站工具、累計成本。"""

    _truck_counter = itertools.count(1)

    def __init__(
        self,
        env: simpy.Environment,
        model: BaselineModel,
        station_positions: Mapping[str, Mapping[str, Any]],
        config: DispatchConfig,
        rng: random.Random | None = None,
        depot_config: Mapping[str, Mapping[str, int]] | None = None,
        station_demand: Mapping[str, float] | None = None,
        station_hourly_demand: Mapping[str, Mapping[int, float]] | None = None,
    ) -> None:
        self.env = env
        self.model = model
        self.positions = station_positions
        self.config = config
        self.rng = rng or random.Random()
        # 逐區集散場初始/容量（{district: {initial, capacity}}）；None 則用 config 的單一值。
        self.depot_config = dict(depot_config or {})
        # 站點日需求（總λ），供 P5 需求加權選站；None → 全 0（等於不加權）。
        self.station_demand = dict(station_demand or {})
        # 站點逐時借走率 λ_rent（{sid:{hour:λ}}），供 P6 預測式預置。
        self.station_hourly_demand = dict(station_hourly_demand or {})
        self.stats = DispatchStats()

        # 每區集散場置於該區站點的「幾何中位數」（最小化到各站加總距離）。
        # 同時用站點 canvas x/y 的幾何中位數當集散場在視覺化畫布上的位置（與 lat/lon 中位數對應同一概念）。
        self.depots: dict[str, Depot] = {}
        self.depot_xy: dict[str, dict[str, float]] = {}
        for district_id, stations in model.stations_by_district.items():
            coords: list[tuple[float, float]] = []
            xy: list[tuple[float, float]] = []
            for sid in stations:
                pos = station_positions.get(sid)
                if pos is None:
                    continue
                coords.append((float(pos["latitude"]), float(pos["longitude"])))
                if "x" in pos and "y" in pos:
                    xy.append((float(pos["x"]), float(pos["y"])))
            if not coords:
                continue
            lat, lon = geometric_median(coords)
            dc = self.depot_config.get(district_id, {})
            self.depots[district_id] = Depot(
                district_id,
                latitude=lat,
                longitude=lon,
                capacity=int(dc.get("capacity", config.depot_capacity)),
                initial=int(dc.get("initial", config.depot_initial)),
            )
            if xy:
                mx, my = weiszfeld(xy)
                self.depot_xy[district_id] = {"x": mx, "y": my}

        # 選站防撞：同一時刻被某卡車鎖定的站點，其他卡車不再重複選取。
        self._claimed: set[str] = set()
        self.truck_count: int = 0
        # 逐區成本記帳（trips / km / on_duty_minutes），供「各區成本佔比、各區 SC ratio」。
        self.district_stats: dict[str, dict[str, float]] = {}

        # ---- 視覺化軌跡記錄 ----
        # truck_legs：每段卡車狀態（移動或停留）；depot_timeline：集散場庫存階梯。
        self.truck_legs: list[dict[str, Any]] = []
        self.depot_timeline: list[dict[str, Any]] = []
        for did, depot in self.depots.items():
            self.depot_timeline.append(
                {"depot": did, "time": float(env.now), "inventory": depot.inventory}
            )

    def record_leg(
        self,
        truck: "DispatchTruck",
        t0: float,
        t1: float,
        from_loc: tuple[str, str],
        to_loc: tuple[str, str],
        load: int,
        moving: bool,
    ) -> None:
        """記錄一段卡車軌跡（供視覺化動畫）。moving=True 為行駛、False 為停留。"""

        self.truck_legs.append({
            "truckId": truck.truck_id,
            "district": truck.district_id,
            "t0": round(float(t0), 4),
            "t1": round(float(t1), 4),
            "fromType": from_loc[0], "fromId": from_loc[1],
            "toType": to_loc[0], "toId": to_loc[1],
            "load": int(load),
            "cap": int(self.config.truck_capacity),
            "moving": bool(moving),
        })

    def record_depot(self, depot: Depot) -> None:
        """記錄集散場庫存變化（階梯函式）。"""

        self.depot_timeline.append(
            {"depot": depot.district_id, "time": round(float(self.env.now), 4),
             "inventory": int(depot.inventory)}
        )

    # ----- 幾何 / 位置 ------------------------------------------------------ #

    def station_position(self, station: Station) -> dict[str, float]:
        pos = self.positions[station.station_id]
        return {"latitude": float(pos["latitude"]), "longitude": float(pos["longitude"])}

    # ----- 站點狀態 / 分型 -------------------------------------------------- #

    def _ratio(self, station: Station) -> float:
        return station.available_bikes / station.capacity if station.capacity else 0.0

    def replenish_target_level(self, station: Station) -> int:
        """補車目標台數（補到 high target）。"""

        return int(round(self.config.target_high_ratio * station.capacity))

    def withdraw_target_level(self, station: Station) -> int:
        """抽車目標台數（抽到 low target）。"""

        return int(round(self.config.target_low_ratio * station.capacity))

    def predicted_outflow(self, station: Station, now_minute: float) -> float:
        """預測未來 horizon 小時該站的借走量（λ_rent 加總）。供 P6 預測式預置。"""

        hourly = self.station_hourly_demand.get(station.station_id)
        if not hourly:
            return 0.0
        start_h = int(now_minute // 60)
        horizon = int(round(self.config.forecast_horizon_hours))
        total = 0.0
        for h in range(start_h, start_h + horizon):
            total += float(hourly.get(h % 24, hourly.get(str(h % 24), 0.0)))
        return total

    def forecast_target(self, station: Station, now_minute: float) -> int:
        """P6 預置目標水位 = min(容量, 基準比率×容量 + 預測流出)。"""

        base = self.config.forecast_base_ratio * station.capacity
        target = base + self.predicted_outflow(station, now_minute)
        return int(min(station.capacity, round(target)))

    def needs_replenish(self, station: Station) -> bool:
        return self._ratio(station) < self.config.lcl_ratio

    def needs_withdraw(self, station: Station) -> bool:
        return self._ratio(station) > self.config.ucl_ratio

    def deviation_bikes(self, station: Station) -> float:
        """站點偏離其對應目標值的車數（用於貪婪選站）。"""

        if self.needs_replenish(station):
            return self.replenish_target_level(station) - station.available_bikes
        if self.needs_withdraw(station):
            return station.available_bikes - self.withdraw_target_level(station)
        return 0.0

    def is_actionable(self, station: Station) -> bool:
        """站點是否「值得服務」：破 UCL/LCL 且「偏離目標 / 容量」≥ min_action_ratio。

        用比率門檻（非絕對台數），讓小容量站不被不公平過濾——小站每台車的比率變化更大，
        對優良時段占比的影響不亞於大站。
        """

        if not (self.needs_replenish(station) or self.needs_withdraw(station)):
            return False
        if not station.capacity:
            return False
        return (self.deviation_bikes(station) / station.capacity) >= self.config.min_action_ratio

    def candidate_stations(self, truck: "DispatchTruck") -> list[Station]:
        """依卡車載量狀態過濾候選站，並排除已被鎖定者與「不值得服務」者（行動量門檻）。

        語意（組員規則）：滿車（載滿『車輛』）→ 只能補車（有車可放、無空間再抽）；
        空車 → 只能抽車（有空間可裝、無車可補）。
        因此：能補車 ⇔ 車上有車 (load > 0)；能抽車 ⇔ 車上有空位 (load < capacity)。
        """

        stations = self.model.stations_by_district.get(truck.district_id, {})
        can_replenish = truck.load > 0
        can_withdraw = truck.load < self.config.truck_capacity
        out: list[Station] = []
        for sid, st in stations.items():
            if sid in self._claimed:
                continue
            if not self.is_actionable(st):
                continue  # 行動量門檻：偏離 < min_action_bikes 不值得服務
            if self.needs_replenish(st) and can_replenish:
                out.append(st)
            elif self.needs_withdraw(st) and can_withdraw:
                out.append(st)
        return out

    def has_unmet_need(self, truck: "DispatchTruck") -> bool:
        """區內是否還有任何「值得服務」的站（破門檻且偏離 ≥ min_action_bikes）。"""

        stations = self.model.stations_by_district.get(truck.district_id, {})
        for sid, st in stations.items():
            if sid in self._claimed:
                continue
            if self.is_actionable(st):
                return True
        return False

    def dynamic_should_dispatch(self, district_id: str) -> bool:
        """Policy 2（教科書原版）觸發：區內有危急站(100% 滿或 0% 空)，或破 UCL/LCL 的站數 ≥ 門檻。"""

        stations = self.model.stations_by_district.get(district_id, {})
        warning = 0
        for st in stations.values():
            bikes = st.available_bikes
            if bikes <= 0 or bikes >= st.capacity:
                return True  # 危急站：完全空或完全滿
            ratio = bikes / st.capacity if st.capacity else 0.0
            if ratio > self.config.ucl_ratio or ratio < self.config.lcl_ratio:
                warning += 1
        return warning >= self.config.dynamic_warning_count

    def alert_should_dispatch(self, district_id: str) -> bool:
        """P3 反應層觸發：破內警戒帶(預設 10/90) 的站數 ≥ 門檻（比 80/20 緊、比 0/100 早）。"""

        stations = self.model.stations_by_district.get(district_id, {})
        cfg = self.config
        alert = 0
        for st in stations.values():
            ratio = st.available_bikes / st.capacity if st.capacity else 0.0
            if ratio <= cfg.alert_low_ratio or ratio >= cfg.alert_high_ratio:
                alert += 1
        return alert >= cfg.dynamic_warning_count

    def plan_move(self, truck: "DispatchTruck"):
        """P7 配對協調：回傳一筆任務。優先就近 surplus→deficit 鏈式（繞過集散場）；
        其次 集散場→赤字（動用集散場儲備）；再次 盈餘→集散場（停放多餘）。None＝無事。

        surplus＝破 UCL 且值得服務、deficit＝破 LCL 且值得服務（排除已被其他車鎖定者）。
        配對 score = 可搬台數 / (車→盈餘 + 盈餘→赤字 距離 + const)，取最大——鏡像區間
        Policy 2 的貪婪最短距離配對，但併入「可搬量」與「車到盈餘的距離」。見 §35。
        """

        stations = self.model.stations_by_district.get(truck.district_id, {})
        surplus: list[Station] = []
        deficit: list[Station] = []
        for sid, st in stations.items():
            if sid in self._claimed or not self.is_actionable(st):
                continue
            if self.needs_withdraw(st):
                surplus.append(st)
            elif self.needs_replenish(st):
                deficit.append(st)
        if not surplus and not deficit:
            return None
        const = self.config.score_distance_const_km_dynamic
        if surplus and deficit:
            best = None
            best_score = -1.0
            for s in surplus:
                s_pos = self.station_position(s)
                s_exc = s.available_bikes - self.withdraw_target_level(s)
                if s_exc <= 0:
                    continue
                d_truck = km_between(truck.position, s_pos)
                for d in deficit:
                    move = min(s_exc, self.replenish_target_level(d) - d.available_bikes)
                    if move <= 0:
                        continue
                    cost = d_truck + km_between(s_pos, self.station_position(d))
                    score = move / (cost + const)
                    if score > best_score:
                        best_score, best = score, (s, d)
            if best is not None:
                return ("pair", best[0], best[1])
        if deficit and truck.depot.inventory > 0:
            d = max(deficit, key=lambda x: self.deviation_bikes(x))
            return ("depot_to", d)
        if surplus:
            s = max(surplus, key=lambda x: self.deviation_bikes(x))
            return ("to_depot", s)
        return None

    # ----- 成本記帳 -------------------------------------------------------- #

    def account_travel(self, km: float, minutes: float) -> None:
        self.stats.total_km += km
        self.stats.total_active_minutes += minutes

    def acc_district(self, district: str, km: float = 0.0, on_duty: float = 0.0, trips: int = 0) -> None:
        """逐區成本記帳（由卡車呼叫；卡車知道自己 district_id）。"""

        ds = self.district_stats.setdefault(
            district, {"trips": 0.0, "km": 0.0, "on_duty_minutes": 0.0}
        )
        ds["km"] += km
        ds["on_duty_minutes"] += on_duty
        ds["trips"] += trips

    def district_cost_breakdown(self) -> dict[str, dict[str, float]]:
        """各區調度成本分項（人力/里程/出車）+ 合計，供成本佔比與各區 SC ratio。"""

        cfg = self.config
        out: dict[str, dict[str, float]] = {}
        for d, s in self.district_stats.items():
            labor = cfg.cost_per_labor_hour * (s["on_duty_minutes"] / 60.0)
            mileage = cfg.cost_per_km * s["km"]
            trip = cfg.cost_per_trip * s["trips"]
            out[d] = {
                "labor": round(labor, 1),
                "mileage": round(mileage, 1),
                "trip": round(trip, 1),
                "total": round(labor + mileage + trip, 1),
                "on_duty_hours": round(s["on_duty_minutes"] / 60.0, 2),
                "km": round(s["km"], 1),
                "trips": int(s["trips"]),
            }
        return out

    def cost_breakdown(self) -> dict[str, float]:
        """調度成本分項：人力(工時)、里程、出車、車隊固定。"""

        cfg = self.config
        labor = cfg.cost_per_labor_hour * (self.stats.on_duty_minutes / 60.0)
        mileage = cfg.cost_per_km * self.stats.total_km
        trip = cfg.cost_per_trip * self.stats.trips
        fixed = cfg.cost_per_truck_fixed * self.truck_count
        return {
            "labor": round(labor, 2),
            "mileage": round(mileage, 2),
            "trip": round(trip, 2),
            "fixed": round(fixed, 2),
            "total": round(labor + mileage + trip + fixed, 2),
        }

    def dispatching_cost(self) -> float:
        return self.cost_breakdown()["total"]

    # ----- 建立卡車 process ------------------------------------------------ #

    def build_trucks(self, trucks_per_district: "int | Mapping[str, int]") -> list["DispatchTruck"]:
        """建立卡車。trucks_per_district 可為單一整數（均勻配置）或 {district: count}（逐區配置）。

        逐區配置（per-district allocation）：不同行政區可有不同車數，反映規模/需求差異。見 §39。
        """

        trucks: list[DispatchTruck] = []
        for district_id, depot in sorted(self.depots.items()):
            if isinstance(trucks_per_district, Mapping):
                count = int(trucks_per_district.get(district_id, 0))
            else:
                count = int(trucks_per_district)
            for _ in range(count):
                trucks.append(
                    DispatchTruck(
                        truck_id=f"T{next(self._truck_counter)}",
                        system=self,
                        depot=depot,
                    )
                )
        self.truck_count = len(trucks)
        return trucks


# --------------------------------------------------------------------------- #
# 卡車
# --------------------------------------------------------------------------- #


class DispatchTruck:
    """單台調度卡車。位置以經緯度表示；移動/搬運皆消耗模擬時間。"""

    def __init__(self, truck_id: str, system: DispatchSystem, depot: Depot) -> None:
        self.truck_id = truck_id
        self.system = system
        self.env = system.env
        self.config = system.config
        self.depot = depot
        self.district_id = depot.district_id
        self.load = 0
        self.position = depot.position  # 起始停在集散場（lat/lon，供距離計算）
        self.loc: tuple[str, str] = ("depot", depot.district_id)  # 目前所在地（供軌跡記錄）
        self.continuous_work_minutes = 0.0  # 連續工作累計（休息/待命時歸零；達上限強制休息）
        # 工時計薪方式：P2/3/4 按「實際工作+待命」累計（True）；P1 按排定班次窗計（False，見其 process）。
        self._accrue_work_pay = True

    # ----- 低階動作 -------------------------------------------------------- #

    def _log(self, event_type: str, **payload: Any) -> None:
        self.system.model.log_event(
            event_type,
            truck_id=self.truck_id,
            district_id=self.district_id,
            **payload,
        )

    def _sample_speed_kmph(self) -> float:
        """對卡車車速做高斯抽樣（截斷下限），模擬路況變異。"""

        cfg = self.config
        speed = self.system.rng.gauss(cfg.truck_speed_kmph, cfg.truck_speed_sd_kmph)
        return max(cfg.truck_speed_min_kmph, speed)

    def _move_to(self, to_loc: tuple[str, str], position: Mapping[str, float]):
        """移動到指定地點（depot/station），消耗 距離/車速 時間、記里程、記軌跡。"""

        t0 = self.env.now
        km = km_between(self.position, position)
        minutes = km / self._sample_speed_kmph() * 60.0
        self.system.account_travel(km, minutes)
        if minutes > 0:
            yield self.env.timeout(minutes)
        # 行駛＝工作：計入連續工作；計薪工時依政策（P1 由班次窗計，不在此累計）。
        worked = self.env.now - t0
        if self._accrue_work_pay:
            self.system.stats.on_duty_minutes += worked
        self.continuous_work_minutes += worked
        self.system.acc_district(self.district_id, km=km, on_duty=worked if self._accrue_work_pay else 0.0)
        # 只在真的有移動（時間推進或換地點）時記錄行駛段，避免零長度雜訊。
        if self.env.now > t0 or self.loc != to_loc:
            self.system.record_leg(self, t0, self.env.now, self.loc, to_loc, self.load, moving=True)
        self.position = {"latitude": position["latitude"], "longitude": position["longitude"]}
        self.loc = to_loc

    def _stay(self, minutes: float):
        """在原地停留一段時間，並記一段停留軌跡（載量為當前值）。純記錄，不計成本。"""

        t0 = self.env.now
        if minutes > 0:
            yield self.env.timeout(minutes)
        if self.env.now > t0:
            self.system.record_leg(self, t0, self.env.now, self.loc, self.loc, self.load, moving=False)

    def _in_duty_window(self) -> bool:
        cfg = self.config
        t = self.env.now % 1440.0  # 對齊到當日（支援跨日模擬）
        windows = cfg.duty_windows or ((cfg.duty_start_minute, cfg.duty_end_minute),)
        return any(start <= t < end for start, end in windows)

    def standby(self, minutes: float):
        """待命（停在集散場）：值勤窗內計薪、窗外不計薪；皆使連續工作歸零。"""

        if minutes <= 0:
            return
        paid = self._in_duty_window()
        yield from self._stay(minutes)
        if paid:
            self.system.stats.on_duty_minutes += minutes
            self.system.acc_district(self.district_id, on_duty=minutes)
        self.continuous_work_minutes = 0.0

    def rest(self, minutes: float):
        """強制休息（不計薪），連續工作歸零。"""

        if minutes <= 0:
            return
        yield from self._stay(minutes)
        self.system.stats.rest_minutes += minutes
        self.continuous_work_minutes = 0.0
        self._log("truck_rest", minutes=round(minutes, 1))

    def maybe_rest(self):
        """連續工作達上限即強制休息（返場休息）。"""

        cfg = self.config
        if self.continuous_work_minutes >= cfg.max_continuous_work_minutes:
            yield from self._move_to(("depot", self.district_id), self.depot.position)
            self.system.record_depot(self.depot)
            yield from self.rest(cfg.rest_minutes)

    def travel_to(self, station: Station):
        """前往某站點（記為一段行駛軌跡）。"""

        yield from self._move_to(
            ("station", station.station_id), self.system.station_position(station)
        )

    def _handle(self, qty: int):
        """搬運時間 = 停車準備 + 單台搬運 × 台數（記為停留軌跡，載量已更新為搬運後）。搬運＝工作（計薪）。"""

        minutes = self.config.handling_setup_min + self.config.handling_per_bike_min * qty
        self.system.stats.total_active_minutes += minutes
        if self._accrue_work_pay:
            self.system.stats.on_duty_minutes += minutes
            self.system.acc_district(self.district_id, on_duty=minutes)
        self.continuous_work_minutes += minutes
        yield from self._stay(minutes)

    def serve_to_target(self, station: Station, target: int, mode: str = ""):
        """把站點補/抽到 target_level（補車 cur<target、抽車 cur>target）。回傳實際搬運台數。

        共用於 band 服務（serve）與 P6 預測式預置（_preposition_loop）。
        在「搬運當下」記錄事件（bikes_after 為剛搬完真實值），時間與車輛變化對齊。
        """

        sys = self.system
        cur = station.available_bikes
        if cur < target:  # 補車
            qty = max(0, int(min(self.load, target - cur, station.available_docks)))
            if qty <= 0:
                self._log("truck_wasted_visit", station_id=station.station_id,
                          reason="replenish_target_met", mode=mode)
                sys.stats.wasted_visits += 1
                return 0
            yield station.docks.get(qty)
            yield station.bikes.put(qty)
            self.load -= qty
            sys.stats.bikes_replenished += qty
            sys.stats.station_visits += 1
            self._log("truck_replenish", station_id=station.station_id, qty=qty,
                      bikes_after=station.available_bikes, truck_load=self.load,
                      depot_inventory=self.depot.inventory, mode=mode)
            yield from self._handle(qty)
            return qty
        if cur > target:  # 抽車
            free = self.config.truck_capacity - self.load
            qty = max(0, int(min(free, cur - target, station.available_bikes)))
            if qty <= 0:
                self._log("truck_wasted_visit", station_id=station.station_id,
                          reason="withdraw_target_met", mode=mode)
                sys.stats.wasted_visits += 1
                return 0
            yield station.bikes.get(qty)
            yield station.docks.put(qty)
            self.load += qty
            sys.stats.bikes_withdrawn += qty
            sys.stats.station_visits += 1
            self._log("truck_withdraw", station_id=station.station_id, qty=qty,
                      bikes_after=station.available_bikes, truck_load=self.load,
                      depot_inventory=self.depot.inventory, mode=mode)
            yield from self._handle(qty)
            return qty
        # 已達 target：白跑一趟。
        self._log("truck_wasted_visit", station_id=station.station_id, reason="at_target", mode=mode)
        sys.stats.wasted_visits += 1
        return 0

    def serve(self, station: Station):
        """band 服務：依現況分型決定 target（<LCL→補到70%、>UCL→抽到30%）後補/抽。"""

        sys = self.system
        if sys.needs_replenish(station):
            target = sys.replenish_target_level(station)
        elif sys.needs_withdraw(station):
            target = sys.withdraw_target_level(station)
        else:
            self._log("truck_wasted_visit", station_id=station.station_id, reason="healthy")
            sys.stats.wasted_visits += 1
            return 0
        return (yield from self.serve_to_target(station, target))

    def reset_at_depot(self, target_load: int | None = None):
        """返回集散場並把載量調整至 target_load（預設起始載量）。"""

        target = self.config.truck_initial_load if target_load is None else int(target_load)
        yield from self._move_to(("depot", self.district_id), self.depot.position)
        if self.load > target:
            self.depot.give(self.load - target)
            self.load = target
        elif self.load < target:
            got = self.depot.take(target - self.load)
            self.load += got
        self.system.record_depot(self.depot)
        self._log("truck_at_depot", load=self.load, depot_inventory=self.depot.inventory)

    # ----- 選站 ------------------------------------------------------------ #

    def select_target(self, squared: bool = False) -> Station | None:
        """選站。取 Score 最大者，同分隨機（兩 Policy 皆隨機）。

        Policy 1（squared=False）：Score = |偏離| / (距離 + 0.1)。
        Policy 2（squared=True） ：Score = (偏離)² / (距離 + 0.5)，平方放大極端、削弱距離。
        """

        cands = self.system.candidate_stations(self)
        if not cands:
            return None
        cfg = self.config
        const = cfg.score_distance_const_km_dynamic if squared else cfg.score_distance_const_km
        alpha = cfg.demand_weight_alpha
        best_score = -1.0
        scores: list[tuple[float, Station]] = []
        for st in cands:
            dev = abs(self.system.deviation_bikes(st))
            base = dev * dev if squared else dev
            dist = km_between(self.position, self.system.station_position(st))
            score = base / (dist + const)
            # P5：需求加權（× (日λ)^alpha）。alpha=0 → ^0=1 無影響；0.5 → √λ。
            if alpha > 0:
                score *= max(self.system.station_demand.get(st.station_id, 0.0), 1.0) ** alpha
            scores.append((score, st))
            best_score = max(best_score, score)
        tied = [st for score, st in scores if best_score - score <= 1e-9]
        return self.system.rng.choice(tied)


# --------------------------------------------------------------------------- #
# 共用：貪婪工作迴圈（四個 policy 共用，避免重複/分歧）
# --------------------------------------------------------------------------- #


def _work_loop(truck: DispatchTruck, squared: bool, deadline: float | None):
    """貪婪補/抽車工作迴圈（出車後執行）。

    - 反覆：（必要時強制休息）→ 選 Score 最大站（squared 決定線性/平方）→ 前往 → 補/抽。
    - 資源耗盡（滿車只剩抽站 / 空車只剩補站）→ 返場重置載量再續。
    - 終止：全區無「值得服務」的站即收工；或（有 deadline 時）超過時窗即收工。
    - 含 stall 防呆，避免零延遲無窮迴圈。
    """

    sys = truck.system
    cfg = truck.config
    stall = 0
    while deadline is None or truck.env.now < deadline:
        yield from truck.maybe_rest()  # 連續工作達上限→返場強制休息
        t_before = truck.env.now
        station = truck.select_target(squared=squared)
        if station is None:
            if not sys.has_unmet_need(truck):
                break  # 全區健康（或剩餘需求被鎖定）→ 收工
            load_before = truck.load
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            if truck.load == load_before and truck.select_target(squared=squared) is None:
                break  # 重置後載量無法改變（集散場耗盡）且仍無候選 → 避免空轉
        else:
            sid = station.station_id
            sys._claimed.add(sid)
            try:
                yield from truck.travel_to(station)
                yield from truck.serve(station)
            finally:
                sys._claimed.discard(sid)
            if not sys.has_unmet_need(truck):
                break  # 全區健康即收工（P1 在時窗內也會提早收工）
        if truck.env.now <= t_before:
            stall += 1
            if stall >= 5:
                yield truck.env.timeout(1.0)
                stall = 0
        else:
            stall = 0


# --------------------------------------------------------------------------- #
# Policy 1：固定巡迴
# --------------------------------------------------------------------------- #


def fixed_patrol_process(truck: DispatchTruck):
    """Policy 1 固定巡迴：每日 2 班、各 patrol_duration 分鐘（線性 score 選站）。

    人力＝排定班次窗計薪（整班計薪，不論班內是否一直在動）。
    """

    cfg = truck.config
    truck._accrue_work_pay = False  # P1 改由整班計薪
    for start in cfg.patrol_start_minutes:
        if truck.env.now < start:
            yield from truck._stay(start - truck.env.now)  # 班外＝下班，不計薪
        deadline = start + cfg.patrol_duration_minutes
        yield from truck.reset_at_depot(cfg.truck_initial_load)
        truck.system.stats.trips += 1
        truck.system.stats.on_duty_minutes += cfg.patrol_duration_minutes
        truck.system.acc_district(truck.district_id, on_duty=cfg.patrol_duration_minutes, trips=1)
        truck._log("truck_depart", load=truck.load, start_minute=start)
        yield from _work_loop(truck, squared=False, deadline=deadline)
        yield from truck.reset_at_depot(cfg.truck_initial_load)
        truck._log("truck_shift_end", load=truck.load)


# --------------------------------------------------------------------------- #
# 共用：值勤窗內的動態待命迴圈（P2 / P3 反應層共用）
# --------------------------------------------------------------------------- #


def _dynamic_standby_loop(truck: DispatchTruck, trigger):
    """在值勤窗內每 scan 分鐘待命掃描；trigger(district)→True 即出車工作；出車後冷卻。

    trigger：判定是否該出車的函式（P2 用 dynamic_should_dispatch、P3 用 alert_should_dispatch）。
    值勤窗外＝下班（不計薪、不出車）。
    """

    sys = truck.system
    cfg = truck.config
    scan = cfg.dynamic_scan_minutes
    while True:
        now = truck.env.now
        dt = scan - (now % scan)
        if dt <= 1e-9:
            dt = scan
        if not truck._in_duty_window():
            yield from truck._stay(dt)  # 下班（不計薪）
            continue
        yield from truck.standby(dt)  # 值勤待命（計薪）
        if not trigger(truck.district_id):
            continue
        yield from truck.reset_at_depot(cfg.truck_initial_load)
        sys.stats.trips += 1
        sys.acc_district(truck.district_id, trips=1)
        truck._log("truck_depart", load=truck.load, trigger="dynamic")
        yield from _work_loop(truck, squared=True, deadline=None)
        yield from truck.reset_at_depot(cfg.truck_initial_load)
        truck._log("truck_shift_end", load=truck.load, trigger="dynamic")
        if cfg.cooldown_minutes > 0:
            yield from truck.standby(cfg.cooldown_minutes)  # 返場冷卻（待命計薪）


# --------------------------------------------------------------------------- #
# Policy 2：動態觸發（教科書原版觸發：危急 100/0 或 ≥3 站破 80/20）
# --------------------------------------------------------------------------- #


def dynamic_trigger_process(truck: DispatchTruck):
    yield from _dynamic_standby_loop(truck, truck.system.dynamic_should_dispatch)


# --------------------------------------------------------------------------- #
# Policy 3：混合（方案甲）＝ 排程預置巡迴 ＋ 反應層（≥3 站破 10/90 警戒帶）
# --------------------------------------------------------------------------- #


def hybrid_anticipatory_process(truck: DispatchTruck):
    """P3：尖峰前排定巡迴做預置；其餘時間對警戒帶(10/90)反應式出車。

    把每日切成數段：到下一個排程巡迴起點前的等待時間，改用「動態待命掃描」填滿
    （值勤窗內待命、觸發即出車），到了排程起點則執行一班固定巡迴。
    """

    sys = truck.system
    cfg = truck.config
    scan = cfg.dynamic_scan_minutes
    starts = list(cfg.patrol_start_minutes)
    idx = 0
    while True:
        next_start = starts[idx] if idx < len(starts) else None
        # 還沒到下一個排程巡迴：在值勤窗內動態待命掃描（破 10/90 警戒帶即出車）。
        if next_start is None or truck.env.now >= next_start:
            if next_start is not None and truck.env.now >= next_start:
                # 到了排程巡迴起點 → 執行一班預置巡迴。
                deadline = next_start + cfg.patrol_duration_minutes
                yield from truck.reset_at_depot(cfg.truck_initial_load)
                sys.stats.trips += 1
                sys.acc_district(truck.district_id, trips=1)
                truck._log("truck_depart", load=truck.load, start_minute=next_start, mode="scheduled")
                yield from _work_loop(truck, squared=True, deadline=deadline)
                yield from truck.reset_at_depot(cfg.truck_initial_load)
                truck._log("truck_shift_end", load=truck.load, mode="scheduled")
                idx += 1
                continue
            # 已無排程巡迴 → 純反應待命掃描一格。
            dt = scan - (truck.env.now % scan)
            if dt <= 1e-9:
                dt = scan
        else:
            # 等到下一個排程起點，但中途每格仍做反應掃描（不錯過警戒）。
            dt = scan - (truck.env.now % scan)
            if dt <= 1e-9:
                dt = scan
            dt = min(dt, next_start - truck.env.now)

        if not truck._in_duty_window():
            yield from truck._stay(dt)
            continue
        yield from truck.standby(dt)
        if sys.alert_should_dispatch(truck.district_id):
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            sys.stats.trips += 1
            sys.acc_district(truck.district_id, trips=1)
            truck._log("truck_depart", load=truck.load, mode="reactive")
            yield from _work_loop(truck, squared=True, deadline=None)
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            truck._log("truck_shift_end", load=truck.load, mode="reactive")
            if cfg.cooldown_minutes > 0:
                yield from truck.standby(cfg.cooldown_minutes)


# --------------------------------------------------------------------------- #
# Policy 4：混合（方案乙）＝ 排定班次（P1 timing）＋ 平方選站 ＋ 全區健康即提早收工
# --------------------------------------------------------------------------- #


def hybrid_smartshift_process(truck: DispatchTruck):
    """P4：沿用 P1 的固定班次（沉穩），但班內改用 P2 平方 score 選站、且全區健康就提早收工（省成本）。"""

    cfg = truck.config
    truck._accrue_work_pay = False  # 與 P1 同：整班計薪
    for start in cfg.patrol_start_minutes:
        if truck.env.now < start:
            yield from truck._stay(start - truck.env.now)
        deadline = start + cfg.patrol_duration_minutes
        yield from truck.reset_at_depot(cfg.truck_initial_load)
        truck.system.stats.trips += 1
        truck.system.stats.on_duty_minutes += cfg.patrol_duration_minutes
        truck.system.acc_district(truck.district_id, on_duty=cfg.patrol_duration_minutes, trips=1)
        truck._log("truck_depart", load=truck.load, start_minute=start, mode="smartshift")
        yield from _work_loop(truck, squared=True, deadline=deadline)  # 平方選站 + 健康即收工
        yield from truck.reset_at_depot(cfg.truck_initial_load)
        truck._log("truck_shift_end", load=truck.load, mode="smartshift")


# --------------------------------------------------------------------------- #
# Policy 6：預測式預置（forecast pre-positioning）＝ 預置 + 反應（P3 保留以利對照）
# --------------------------------------------------------------------------- #


def _preposition_loop(truck: DispatchTruck, deadline: float):
    """預置流程：把站點驅向 forecast_target（基準 + 預測流出）。

    與 _work_loop 不同：目標是「預測期末理想位」而非健康帶；補/抽皆朝 forecast_target。
    """

    sys = truck.system
    cfg = truck.config
    thr = cfg.min_action_ratio
    const = cfg.score_distance_const_km

    def deviation(st: Station) -> int:
        return sys.forecast_target(st, truck.env.now) - st.available_bikes  # >0 需補、<0 需抽

    def feasible(st: Station, dev: int) -> bool:
        if abs(dev) < thr * st.capacity:
            return False
        if dev > 0 and truck.load <= 0:
            return False  # 需補但無車
        if dev < 0 and truck.load >= cfg.truck_capacity:
            return False  # 需抽但滿車
        return True

    stall = 0
    while truck.env.now < deadline:
        yield from truck.maybe_rest()
        t_before = truck.env.now
        stations = sys.model.stations_by_district.get(truck.district_id, {})
        cands = [
            (st, deviation(st)) for sid, st in stations.items() if sid not in sys._claimed
        ]
        actionable = [(st, d) for st, d in cands if feasible(st, d)]
        if not actionable:
            # 仍有偏離但被載量卡住 → 返場重置；否則收工。
            any_need = any(abs(d) >= thr * st.capacity for st, d in cands)
            if not any_need:
                break
            load_before = truck.load
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            if truck.load == load_before:
                break
            continue
        best = max(
            actionable,
            key=lambda c: abs(c[1]) / (km_between(truck.position, sys.station_position(c[0])) + const),
        )[0]
        sid = best.station_id
        sys._claimed.add(sid)
        try:
            yield from truck.travel_to(best)
            yield from truck.serve_to_target(best, sys.forecast_target(best, truck.env.now), mode="preposition")
        finally:
            sys._claimed.discard(sid)
        if truck.env.now <= t_before:
            stall += 1
            if stall >= 5:
                yield truck.env.timeout(1.0)
                stall = 0
        else:
            stall = 0


def hybrid_forecast_process(truck: DispatchTruck):
    """P6：預置時點用 λ 預測做預先佈署；其餘時間反應層（≥3 站破 10/90、band 選站）。P3 仍保留以利對照。"""

    sys = truck.system
    cfg = truck.config
    scan = cfg.dynamic_scan_minutes
    starts = sorted(cfg.preposition_minutes)
    idx = 0
    # 06:00 快照起跑時，起點前的預置時點（如 05:30）視為「現實已整備」→ 跳過，不補做。
    # 見 architecture_narrative §32。
    while idx < len(starts) and starts[idx] < truck.env.now:
        idx += 1
    while True:
        next_start = starts[idx] if idx < len(starts) else None
        if next_start is not None and truck.env.now >= next_start:
            # 預置時點 → 跑一輪 forecast 預置（時間預算 = patrol_duration）。
            deadline = next_start + cfg.patrol_duration_minutes
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            sys.stats.trips += 1
            sys.acc_district(truck.district_id, trips=1)
            truck._log("truck_depart", load=truck.load, start_minute=next_start, mode="preposition")
            yield from _preposition_loop(truck, deadline)
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            truck._log("truck_shift_end", load=truck.load, mode="preposition")
            idx += 1
            continue
        # 反應層待命掃描一格（到下個預置時點前不超過）。
        dt = scan - (truck.env.now % scan)
        if dt <= 1e-9:
            dt = scan
        if next_start is not None:
            dt = min(dt, next_start - truck.env.now)
        if not truck._in_duty_window():
            yield from truck._stay(dt)
            continue
        yield from truck.standby(dt)
        if sys.alert_should_dispatch(truck.district_id):
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            sys.stats.trips += 1
            sys.acc_district(truck.district_id, trips=1)
            truck._log("truck_depart", load=truck.load, mode="reactive")
            yield from _work_loop(truck, squared=True, deadline=None)
            yield from truck.reset_at_depot(cfg.truck_initial_load)
            truck._log("truck_shift_end", load=truck.load, mode="reactive")
            if cfg.cooldown_minutes > 0:
                yield from truck.standby(cfg.cooldown_minutes)


# --------------------------------------------------------------------------- #
# Policy 7：配對協調（全域 surplus→deficit 鏈式 + 去重指派）
# --------------------------------------------------------------------------- #


def pair_coord_process(truck: DispatchTruck):
    """P7 配對協調：把區間 Policy 2「虛擬結算法」的全域 flow 配對思路套到**區內站點層**，
    攻多車協調損失。每次掃描由 `plan_move` 全域配對最划算的 surplus→deficit、卡車鏈式
    「過滿站取車→缺車站放車」（繞過集散場往返）；無配對時改用集散場儲備或停放多餘。
    待命族（全天值勤、每 scan 重解）。見 architecture_narrative §35。
    """

    sys = truck.system
    cfg = truck.config
    scan = cfg.dynamic_scan_minutes
    departed = False
    stall = 0
    while True:
        if not truck._in_duty_window():
            departed = False
            yield from truck._stay(scan)  # 班外＝下班
            continue
        yield from truck.maybe_rest()
        t_before = truck.env.now
        move = sys.plan_move(truck)
        if move is None:
            if truck.load > 0 and truck.loc[0] != "depot":
                yield from truck.reset_at_depot(0)  # 無事可做→卸回集散場（守恆）
            departed = False
            yield from truck.standby(scan)
            continue
        if not departed:
            sys.stats.trips += 1
            sys.acc_district(truck.district_id, trips=1)
            truck._log("truck_depart", load=truck.load, mode="paircoord")
            departed = True
        kind = move[0]
        if kind == "pair":
            _, s, d = move
            sys._claimed.add(s.station_id)
            sys._claimed.add(d.station_id)
            try:
                yield from truck.travel_to(s)
                yield from truck.serve_to_target(s, sys.withdraw_target_level(s), mode="pair_pickup")
                yield from truck.travel_to(d)
                yield from truck.serve_to_target(d, sys.replenish_target_level(d), mode="pair_deliver")
            finally:
                sys._claimed.discard(s.station_id)
                sys._claimed.discard(d.station_id)
        elif kind == "depot_to":
            _, d = move
            sys._claimed.add(d.station_id)
            try:
                need = sys.replenish_target_level(d) - d.available_bikes
                if truck.load < need:  # 載量不足→先回集散場補滿可用量
                    yield from truck.reset_at_depot(min(cfg.truck_capacity, max(truck.load, need)))
                yield from truck.travel_to(d)
                yield from truck.serve_to_target(d, sys.replenish_target_level(d), mode="depot_deliver")
            finally:
                sys._claimed.discard(d.station_id)
        else:  # to_depot：盈餘站取車後停回集散場
            _, s = move
            sys._claimed.add(s.station_id)
            try:
                yield from truck.travel_to(s)
                yield from truck.serve_to_target(s, sys.withdraw_target_level(s), mode="surplus_pickup")
                yield from truck.reset_at_depot(0)
            finally:
                sys._claimed.discard(s.station_id)
        if truck.env.now <= t_before:  # stall 防呆（零延遲迭代）
            stall += 1
            if stall >= 5:
                yield truck.env.timeout(1.0)
                stall = 0
        else:
            stall = 0


# --------------------------------------------------------------------------- #
# 最佳化上界（optimal_ub）：每區一個控制器，完美自由調度（忽略集散站物流/路徑），
# 僅受車隊吞吐量限制。代表「給定車隊的理論服務天花板」。見 architecture_narrative §31。
# --------------------------------------------------------------------------- #


def optimal_ub_controller(system: "DispatchSystem", district_id: str, throughput_trucks: float):
    """完美自由調度上界：每 5 分鐘把各站推向 50%，集散池為免費瞬間無限緩衝，僅受吞吐預算。

    - 守恆：surplus 站 → 集散池、集散池 → deficit 站（總車數不變）。
    - 自由路由（無行駛時間）、忽略集散站容量/物流 → 合法上界（任何真實政策皆 ≤ 它）。
    - 吞吐預算/間隔 = throughput_trucks × (容量/搬運時間) × 5min；throughput_trucks≤0 → 無限（UB-∞）。
    - 搬移以 truck_replenish/withdraw 事件記錄（mode="ub"），讓 run_metrics 正確重建站點時序。
    """

    env = system.env
    cfg = system.config
    stations = list(system.model.stations_by_district.get(district_id, {}).values())
    depot = system.depots.get(district_id)
    if not stations or depot is None:
        return
    if throughput_trucks and throughput_trucks > 0:
        rate_per_min = throughput_trucks * (
            cfg.truck_capacity / (cfg.handling_setup_min + cfg.handling_per_bike_min * cfg.truck_capacity)
        )
        budget = rate_per_min * cfg.dynamic_scan_minutes
    else:
        budget = float("inf")  # UB-∞

    def target(st: Station) -> int:
        return int(round(0.5 * st.capacity))  # 健康帶中點

    while True:
        yield env.timeout(cfg.dynamic_scan_minutes)
        moved = 0.0
        # Phase 1：把 surplus（>50%）的多餘車瞬間收進集散池。
        for st in stations:
            if moved >= budget:
                break
            extra = st.available_bikes - target(st)
            if extra > 0:
                q = int(min(extra, budget - moved))
                if q > 0:
                    yield st.bikes.get(q)
                    yield st.docks.put(q)
                    depot.inventory += q  # 上界：集散池無容量上限
                    moved += q
                    system.stats.bikes_withdrawn += q
                    system.model.log_event("truck_withdraw", truck_id="UB", district_id=district_id,
                                           station_id=st.station_id, qty=q,
                                           bikes_after=st.available_bikes, mode="ub")
        # Phase 2：從集散池瞬間補給 deficit（<50%），最缺者優先。
        deficits = sorted(
            ((target(st) - st.available_bikes, st) for st in stations if st.available_bikes < target(st)),
            key=lambda x: -x[0],
        )
        for need, st in deficits:
            if moved >= budget or depot.inventory <= 0:
                break
            q = int(min(need, depot.inventory, budget - moved, st.available_docks))
            if q > 0:
                yield st.docks.get(q)
                yield st.bikes.put(q)
                depot.inventory -= q
                moved += q
                system.stats.bikes_replenished += q
                system.model.log_event("truck_replenish", truck_id="UB", district_id=district_id,
                                       station_id=st.station_id, qty=q,
                                       bikes_after=st.available_bikes, mode="ub")
        system.record_depot(depot)


# --------------------------------------------------------------------------- #
# 對外組裝介面
# --------------------------------------------------------------------------- #


# 全部 policy 保留，方便日後反覆比較（none = 無調度 baseline）。
POLICIES = {
    "fixed": fixed_patrol_process,                  # Policy 1 固定巡迴
    "dynamic": dynamic_trigger_process,             # Policy 2 動態觸發
    "hybrid_anticipatory": hybrid_anticipatory_process,  # Policy 3 排程預置+反應
    "hybrid_smartshift": hybrid_smartshift_process,      # Policy 4 排定班次+平方選站+提早收工
    "hybrid_forecast": hybrid_forecast_process,          # Policy 6 預測式預置+反應
    "pair_coord": pair_coord_process,                    # Policy 7 配對協調（全域 surplus→deficit 鏈式）
}


def attach_dispatch(
    env: simpy.Environment,
    model: BaselineModel,
    station_positions: Mapping[str, Mapping[str, Any]],
    policy: str,
    trucks_per_district: "int | Mapping[str, int]",
    config: DispatchConfig | None = None,
    rng: random.Random | None = None,
    depot_config: Mapping[str, Mapping[str, int]] | None = None,
    station_demand: Mapping[str, float] | None = None,
    station_hourly_demand: Mapping[str, Mapping[int, float]] | None = None,
) -> DispatchSystem:
    """建立調度系統、卡車並註冊其 SimPy process。回傳 DispatchSystem（含 stats）。

    policy="none" 時不掛任何卡車（無調度 baseline）。
    depot_config：逐區集散場 {district:{initial,capacity}}；None 用 config 單一值。
    station_demand：站點日需求（供 P5 需求加權選站）。
    station_hourly_demand：站點逐時 λ_rent（供 P6 預測式預置）。
    """

    config = config or DispatchConfig()
    system = DispatchSystem(
        env, model, station_positions, config, rng=rng,
        depot_config=depot_config, station_demand=station_demand,
        station_hourly_demand=station_hourly_demand,
    )
    if policy in ("none", None):
        return system
    if policy == "optimal_ub":
        # 最佳化上界：每區一個控制器（非卡車），完美自由調度、僅受吞吐限制。
        # trucks_per_district <= 0 → UB-∞（無限吞吐）。
        system.truck_count = max(0, trucks_per_district) * len(system.depots)
        for district_id in system.depots:
            env.process(optimal_ub_controller(system, district_id, trucks_per_district))
        return system
    if policy not in POLICIES:
        raise ValueError(f"Unknown dispatch policy: {policy!r}. Options: {sorted(POLICIES)}")
    process_fn = POLICIES[policy]
    for truck in system.build_trucks(trucks_per_district):
        env.process(process_fn(truck))
    return system
