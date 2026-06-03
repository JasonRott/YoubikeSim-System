"""真實 YouBike 站點資料的 scenario runner（需求 + 可選調度政策 + 成本/SC Ratio）。

本腳本使用 clean inputs + 真實 capacity / initial bikes：
- arrival rates（weekday/weekend）、transition matrices、station_positions、station_capacity。

支援 `--dispatch-policy`：none（無調度 baseline）/ fixed(P1) / dynamic(P2) /
hybrid_anticipatory(P3) / hybrid_smartshift(P4)。輸出每站時序指標、優良時段占比（對照真實
true standard 0.607）、time-to-fail、調度成本分項（人力/里程/出車）與 SC Ratio，以及調度軌跡
（供視覺化）。報告可用 `--report-subdir` 分實驗歸檔。詳見 system_upgrade/architecture_narrative.md。
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
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
    load_hourly_lambda_by_station,
    load_station_exit_route_planner,
    make_distance_based_station_selector,
)
from youbike_sim.run_metrics import (  # noqa: E402
    build_narrative,
    compute_run_metrics,
    format_console,
    format_report_lines,
    per_day_excellent,
    per_day_service_level,
)
from youbike_sim.dispatch import DispatchConfig, attach_dispatch  # noqa: E402


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_depot_config(
    station_positions: dict[str, dict[str, Any]],
    capacity_by_station: dict[str, int],
    initial_by_station: dict[str, int],
    rent_lambda_by_station: dict[str, Any],
    total_fleet: int,
    maintenance_fraction: float,
    distribution: str,
    headroom: float,
) -> dict[str, dict[str, int]]:
    """逐區集散場初始/容量（三桶守恆：站點 + 集散場 + 維修 = 全車隊）。

    集散場總量 = total_fleet − 站點初始總和 − 維修車（total_fleet × maintenance_fraction）。
    依 distribution（'demand' 用各區 λ 總和 / 'capacity' 用各區站點容量）分配到各區；
    容量上限 = 初始 × headroom（留 sink 區吸收空間）。見 architecture_narrative §27。
    """

    cap_by_d: dict[str, int] = {}
    dem_by_d: dict[str, float] = {}
    for sid, pos in station_positions.items():
        d = pos["district"]
        cap_by_d[d] = cap_by_d.get(d, 0) + int(capacity_by_station.get(sid, 0))
        hl = rent_lambda_by_station.get(sid, {})
        lam = sum(float(x) for x in (hl.values() if isinstance(hl, dict) else hl)) if hl else 0.0
        dem_by_d[d] = dem_by_d.get(d, 0.0) + lam

    station_init_total = sum(int(v) for v in initial_by_station.values())
    maintenance = round(total_fleet * maintenance_fraction)
    depot_total = max(0, total_fleet - station_init_total - maintenance)

    weights = dem_by_d if distribution == "demand" else cap_by_d
    total_w = sum(weights.values()) or 1.0
    config: dict[str, dict[str, int]] = {}
    for d in cap_by_d:
        init = int(round(depot_total * weights.get(d, 0) / total_w))
        config[d] = {"initial": init, "capacity": max(init, int(round(init * headroom)))}
    return config


def allocate_trucks(
    station_positions: dict[str, dict[str, Any]],
    rent_lambda_by_station: dict[str, Any],
    total: int,
    basis: str,
    min_per: int = 1,
) -> dict[str, int]:
    """把 total 台車逐區配置（per-district allocation，見 §39）。

    basis='stations' → ∝ 各區站數；'demand' → ∝ 各區借車需求 λ 總和。
    每區至少 min_per 台；用最大餘數法讓整數配置剛好加總 = total。
    """

    weight: dict[str, float] = {}
    for sid, pos in station_positions.items():
        d = pos["district"]
        if basis == "stations":
            weight[d] = weight.get(d, 0.0) + 1.0
        elif basis == "demand":
            hl = rent_lambda_by_station.get(sid, {})
            lam = sum(float(x) for x in (hl.values() if isinstance(hl, dict) else hl)) if hl else 0.0
            weight[d] = weight.get(d, 0.0) + lam
        else:
            raise ValueError(f"未知配置基準：{basis}")
    districts = sorted(weight)
    n = len(districts)
    if total < min_per * n:
        raise ValueError(f"total={total} 不足以讓 {n} 區各 {min_per} 台")
    alloc = {d: min_per for d in districts}
    remaining = total - min_per * n
    total_w = sum(weight.values()) or 1.0
    raw = {d: remaining * weight[d] / total_w for d in districts}
    floor = {d: int(raw[d]) for d in districts}
    for d in districts:
        alloc[d] += floor[d]
    leftover = remaining - sum(floor.values())
    for d in sorted(districts, key=lambda x: raw[x] - floor[x], reverse=True)[:leftover]:
        alloc[d] += 1
    return alloc


def depot_avg_inventory(
    depot_timeline: list[dict[str, Any]], sim_minutes: float, window_start: float = 0.0
) -> dict[str, float]:
    """各區集散場「全程時間加權平均庫存」。depot_timeline 為庫存階梯（{depot,time,inventory}）。

    集散場狀況視為成本/觀察項，不是品質標準；此指標用來觀察各區集散場一天的鬆緊。
    window_start：積分起點（06:00 快照起跑時為 360），分母用 sim_minutes − window_start。
    """

    span = sim_minutes - window_start
    by_depot: dict[str, list[tuple[float, int]]] = {}
    for e in depot_timeline:
        by_depot.setdefault(e["depot"], []).append((float(e["time"]), int(e["inventory"])))
    out: dict[str, float] = {}
    for depot, steps in by_depot.items():
        steps.sort(key=lambda x: x[0])
        area = 0.0
        for i, (t, inv) in enumerate(steps):
            t_next = steps[i + 1][0] if i + 1 < len(steps) else sim_minutes
            area += inv * max(0.0, t_next - max(t, window_start))
        out[depot] = round(area / span, 1) if span > 0 else 0.0
    return out


def load_station_capacity(path: Path) -> dict[str, dict[str, Any]]:
    """載入逐站真實 capacity / initial bikes / benchmark；檔案不存在時回傳空 dict。"""

    if not path.exists():
        return {}
    return load_json(path)


def load_station_snapshot(path: Path) -> dict[str, int]:
    """載入真實 06:00 站點車輛快照（{station_id: bikes}）。見 architecture_narrative §32。

    支援 JSON（{sid: bikes} 或 {sid: {bikes/available_bikes/...}}）與 CSV（自動偵測站點代號欄
    與車輛數欄）。組員快照格式未定前採容錯解析；實際檔到手後若欄名不同可再微調。
    """

    if not path.exists():
        raise FileNotFoundError(f"snapshot 檔不存在：{path}")

    id_keys = ("station_id", "sid", "sno", "sna", "代號", "站點代號", "站點", "id")
    bike_keys = (
        "bikes", "available_bikes", "initial_bikes", "sbi", "available_rent_bikes",
        "可借車輛數", "可借", "車輛數", "available", "quantity", "num_bikes",
    )

    def pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
        low = {str(k).strip().lower(): v for k, v in row.items()}
        for k in keys:
            if k.lower() in low and str(low[k.lower()]).strip() != "":
                return low[k.lower()]
        return None

    out: dict[str, int] = {}
    if path.suffix.lower() == ".json":
        data = load_json(path)
        for sid, val in data.items():
            if isinstance(val, dict):
                bikes = pick(val, bike_keys)
            else:
                bikes = val
            if bikes is not None:
                out[str(sid)] = int(round(float(bikes)))
        return out

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            sid = pick(row, id_keys)
            bikes = pick(row, bike_keys)
            if sid is None or bikes is None:
                # 退回前兩欄（無可辨識欄名時）。
                vals = [v for v in row.values()]
                if len(vals) >= 2 and sid is None:
                    sid = vals[0]
                if len(vals) >= 2 and bikes is None:
                    bikes = vals[1]
            if sid is None or bikes is None:
                continue
            try:
                out[str(sid).strip()] = int(round(float(bikes)))
            except ValueError:
                continue
    if not out:
        raise ValueError(f"snapshot 解析後為空（欄名未對上？）：{path}")
    return out


def load_excellent_benchmark(path: Path, profile: str | None = None) -> dict[str, Any] | None:
    """從真實「站點優良時段比例」CSV 算出 benchmark 統計，供模擬指標對照。

    CSV 欄位：站點代號、日期、單日優良時段占比（每站每日一列）。
    profile='weekday'/'weekend' 時，依日期星期幾過濾出該類日子（週一-五／週六日）→
    **週間/週末各自專屬 benchmark**（見 §41）；None 或其他＝全部日子合併。
    """

    if not path.exists():
        return None
    want_weekend = {"weekend": True, "weekday": False}.get(profile)
    values: list[float] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        next(reader, None)  # header
        for row in reader:
            if len(row) >= 3 and row[2]:
                if want_weekend is not None:
                    try:
                        if (datetime.fromisoformat(row[1].strip()).weekday() >= 5) != want_weekend:
                            continue
                    except (ValueError, IndexError):
                        continue
                try:
                    values.append(float(row[2]))
                except ValueError:
                    continue
    if not values:
        return None
    ordered = sorted(values)

    def pct(p: float) -> float:
        return ordered[int(p / 100 * (len(ordered) - 1))]

    return {
        "station_days": len(values),
        "mean": round(sum(values) / len(values), 4),
        "median": round(pct(50), 4),
        "p5": round(pct(5), 4),
        "p10": round(pct(10), 4),
        "p90": round(pct(90), 4),
        "frac_all_day_healthy": round(sum(1 for v in values if v >= 0.999) / len(values), 4),
        "frac_extreme_bad": round(sum(1 for v in values if v <= 0.25) / len(values), 4),
    }


# travel time 機率化預設參數（見 system_upgrade/architecture_narrative.md 第 7 節）。
DEFAULT_CIRCUITY_MEAN = 1.3   # 道路距離 / 直線距離 的繞路係數均值（都市約 1.2–1.4）。
DEFAULT_CIRCUITY_SD = 0.15    # 繞路係數標準差（不是每個人都走最短路）。
DEFAULT_SPEED_MEAN_KMPH = 14.0  # 車速均值（含號誌停等的都市實際騎乘約 12–15 km/h）。
DEFAULT_SPEED_SD_KMPH = 3.0   # 車速標準差。
# 截斷在 ±TRUNC_N_SD 個標準差（用重抽而非夾住，避免在邊界堆出尖峰）。
TRUNC_N_SD = 3.0
CIRCUITY_HARD_MIN = 1.0  # 幾何下限：道路距離不可能短於直線，故 circuity ≥ 1。
SPEED_HARD_MIN_KMPH = 3.0  # 車速物理下限，避免極端慢造成不合理長時間。

# 車輛佔用時間（= 借出到歸還 = 站點不可用時長）校準參數。
# 校準依據：台北官方整體平均租借 27.1 分；文獻同站來回 ≈ 1.8× 單程（40.35/22.47）。
# 解出：單程 ~25 分、同站來回 ~45 分（週間 10.9% 同站加權回 27.2 分，對上台北）。
# 單程佔用 = max(純騎乘時間, 對數常態抽樣)（保留地理依賴為下限）。
DEFAULT_RENTAL_LOGNORM_MU = 2.74   # 單程：mean≈25 分、mode≈6 分。
DEFAULT_RENTAL_LOGNORM_SIGMA = 0.975
# 同站來回（休閒/購物繞圈）佔用較長：mean≈45 分、mode≈17 分（文獻 round trip 較長）。
DEFAULT_SELF_LOGNORM_MU = 3.48
DEFAULT_SELF_LOGNORM_SIGMA = 0.81
DEFAULT_RENTAL_CAP_MINUTES = 180.0  # 佔用時間上限，避免對數常態尾巴出現荒謬長租。


def truncated_gauss(
    rng: random.Random,
    mean: float,
    sd: float,
    n_sd: float = TRUNC_N_SD,
    hard_min: float | None = None,
) -> float:
    """以重抽方式取得截斷常態樣本（落在 [mean-n_sd*sd, mean+n_sd*sd]）。

    若指定 hard_min（物理下限），下界取 max(下界, hard_min)。用重抽（rejection）
    而非夾住（clamp），可避免樣本在邊界值堆出不自然的尖峰。
    """

    low = mean - n_sd * sd
    high = mean + n_sd * sd
    if hard_min is not None:
        low = max(low, hard_min)
    if sd <= 0 or high <= low:
        return max(low, min(high, mean))
    for _ in range(32):
        value = rng.gauss(mean, sd)
        if low <= value <= high:
            return value
    return min(max(rng.gauss(mean, sd), low), high)


def euclidean_km(
    origin_station: Station,
    destination_station: Station,
    station_positions: dict[str, dict[str, Any]],
) -> float:
    """用經緯度近似兩站之間的直線距離（公里）。"""

    origin = station_positions[origin_station.station_id]
    destination = station_positions[destination_station.station_id]
    mean_latitude = math.radians((origin["latitude"] + destination["latitude"]) / 2)
    meters_per_degree_lat = 110_540.0
    meters_per_degree_lon = 111_320.0 * math.cos(mean_latitude)
    dx = (destination["longitude"] - origin["longitude"]) * meters_per_degree_lon
    dy = (destination["latitude"] - origin["latitude"]) * meters_per_degree_lat
    return math.sqrt(dx * dx + dy * dy) / 1000.0


def sample_ride_minutes(
    kilometers: float,
    rng: random.Random,
    circuity_mean: float,
    circuity_sd: float,
    speed_mean_kmph: float,
    speed_sd_kmph: float,
    min_minutes: float,
) -> float:
    """以機率方式抽樣一趟「純騎乘」時間（分鐘）。

    道路距離 = 直線距離 × 繞路係數（高斯，截斷 ≥ 1）；
    車速另以高斯抽樣（截斷到合理範圍，反映號誌與個人差異）。
    純騎乘時間 = 道路距離 / 車速。
    """

    # circuity 截斷在 ±3 SD（重抽），但幾何下限 1.0：道路距離不可能短於直線。
    circuity = truncated_gauss(
        rng, circuity_mean, circuity_sd, TRUNC_N_SD, hard_min=CIRCUITY_HARD_MIN
    )
    speed = truncated_gauss(
        rng, speed_mean_kmph, speed_sd_kmph, TRUNC_N_SD, hard_min=SPEED_HARD_MIN_KMPH
    )
    road_kilometers = kilometers * circuity
    return max(min_minutes, road_kilometers / speed * 60.0)


def sample_occupancy_minutes(
    ride_minutes: float,
    rng: random.Random,
    lognorm_mu: float,
    lognorm_sigma: float,
    cap_minutes: float,
) -> float:
    """抽樣「車輛被占用（不可用）」總時長 = 借出到歸還。

    校準到真實租借時長分布（對數常態），並以純騎乘時間為下限，
    讓多數行程由持有行為主導、但長程行程至少要花得起騎乘時間。
    """

    rental = min(cap_minutes, rng.lognormvariate(lognorm_mu, lognorm_sigma))
    return max(ride_minutes, rental)


def build_real_model(
    env: simpy.Environment,
    rng: random.Random,
    station_positions: dict[str, dict[str, Any]],
    transition_dir: Path,
    profile: str,
    capacity_by_station: dict[str, int],
    initial_by_station: dict[str, int],
    default_capacity: int,
    default_initial_bikes: int,
    speed_kmph: float,
    min_trip_minutes: float,
    circuity_mean: float,
    circuity_sd: float,
    speed_sd_kmph: float,
    rental_lognorm_mu: float,
    rental_lognorm_sigma: float,
    rental_cap_minutes: float,
    self_station_ratio: float,
    self_lognorm_mu: float,
    self_lognorm_sigma: float,
) -> tuple[BaselineModel, list[Station]]:
    """建立真實站點 baseline model（capacity / initial bikes 逐站設定）。"""

    stations = [
        Station(
            env,
            station_id,
            capacity=capacity_by_station.get(station_id, default_capacity),
            initial_bikes=initial_by_station.get(station_id, default_initial_bikes),
            district_id=position["district"],
        )
        for station_id, position in sorted(station_positions.items())
    ]
    districts = sorted({position["district"] for position in station_positions.values()})
    dummy_nodes = [
        DummyNode(env, district, inter_dist_prob={}, intra_dist_prob={})
        for district in districts
    ]
    route_planner = load_station_exit_route_planner(
        transition_dir, profile, self_station_ratio=self_station_ratio
    )

    def ride_minutes(origin_station: Station, destination_station: Station) -> float:
        kilometers = euclidean_km(origin_station, destination_station, station_positions)
        return sample_ride_minutes(
            kilometers,
            rng,
            circuity_mean,
            circuity_sd,
            speed_kmph,
            speed_sd_kmph,
            min_trip_minutes,
        )

    def route_leg_times(
        origin_station: Station,
        origin_dummy: DummyNode,
        destination_dummy: DummyNode,
        destination_station: Station,
    ) -> tuple[float, float, float]:
        # 主行程時間 = 車輛被占用總時長。依「起=終（同站來回）」與否分流校準。
        if destination_station.station_id == origin_station.station_id:
            # 同站來回（休閒繞圈）：距離≈0，佔用由較長的同站分布決定，不套騎乘下限。
            total_minutes = min(
                rental_cap_minutes,
                rng.lognormvariate(self_lognorm_mu, self_lognorm_sigma),
            )
        else:
            ride = ride_minutes(origin_station, destination_station)
            total_minutes = sample_occupancy_minutes(
                ride, rng, rental_lognorm_mu, rental_lognorm_sigma, rental_cap_minutes
            )
        if origin_station.district_id == destination_station.district_id:
            return total_minutes / 2.0, 0.0, total_minutes / 2.0
        return total_minutes * 0.25, total_minutes * 0.5, total_minutes * 0.25

    travel_times = TravelTimeFunctions(
        station_to_dummy=lambda station, dummy: 0.0,
        dummy_to_dummy=lambda origin_dummy, destination_dummy: 0.0,
        dummy_to_station=lambda dummy, station: 0.0,
        # 滿站改去鄰站只算「額外騎乘」，不再疊加一次持有時間（避免重複計算占用）。
        station_to_station=ride_minutes,
        route_leg_times=route_leg_times,
    )

    def projected_distance(origin_station: Station, candidate_station: Station) -> float:
        origin = station_positions[origin_station.station_id]
        candidate = station_positions[candidate_station.station_id]
        return math.hypot(candidate["x"] - origin["x"], candidate["y"] - origin["y"])

    model = BaselineModel(
        env=env,
        stations=stations,
        dummy_nodes=dummy_nodes,
        travel_time_functions=travel_times,
        rng=rng,
        nearest_station_selector=make_distance_based_station_selector(
            distance_func=projected_distance,
            rng=rng,
        ),
        route_planner=route_planner,
    )
    return model, stations


def summarize_events(
    model: BaselineModel,
    simulation_minutes: float,
    uses_real_capacity: bool,
    speed_kmph: float,
    profile: str,
) -> dict[str, Any]:
    """把大型 event log 壓成 summary。"""

    event_counts: dict[str, int] = {}
    routes_by_district: dict[str, int] = {}
    for event in model.event_log:
        event_type = event["event_type"]
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type == "route_planned":
            key = f"{event['origin_district_id']}->{event['destination_district_id']}"
            routes_by_district[key] = routes_by_district.get(key, 0) + 1

    snapshots = model.station_snapshots()
    capacities = [int(s["capacity"]) for s in snapshots if s.get("capacity")]
    capacity_stats = {
        "min": min(capacities) if capacities else None,
        "max": max(capacities) if capacities else None,
        "mean": round(sum(capacities) / len(capacities), 2) if capacities else None,
    }

    return {
        "scenario": "real_system_baseline",
        "profile": profile,
        "simulation_minutes": simulation_minutes,
        "simulation_hours": simulation_minutes / 60.0,
        "station_count": len(model.stations),
        "district_count": len(model.stations_by_district),
        "uses_real_capacity": uses_real_capacity,
        "capacity_stats": capacity_stats,
        "speed_kmph_assumption": speed_kmph,
        "event_counts": dict(sorted(event_counts.items())),
        "observed_total_arrivals": event_counts.get("rider_arrival", 0),
        "successful_routes": event_counts.get("route_planned", 0),
        "shortage_events": event_counts.get("shortage", 0),
        "full_station_events": event_counts.get("full_station", 0),
        "route_planner_fallback_events": event_counts.get("route_planner_fallback", 0),
        "routes_by_district": dict(sorted(routes_by_district.items())),
        "station_snapshots": snapshots,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """寫出 event/snapshot CSV。"""

    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = sorted({field for row in rows for field in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown_report(
    report_dir: Path,
    summary: dict[str, Any],
    metric_lines: list[str],
) -> None:
    """輸出人類可讀的 real scenario 報告（含數據結果分析）。"""

    capacity_stats = summary.get("capacity_stats", {})
    capacity_source = (
        "真實營運資料（dynamic Quantity + kpis avg_fill_ratio）"
        if summary.get("uses_real_capacity")
        else "假設值"
    )
    policy_name = {
        "none": "無調度 baseline", "fixed": "P1 固定巡迴", "dynamic": "P2 動態觸發",
        "hybrid_anticipatory": "P3 預置+反應", "hybrid_smartshift": "P4 智慧班次",
        "hybrid_forecast": "P6 預測式預置", "pair_coord": "P7 配對協調", "optimal_ub": "最佳化上界",
    }.get(summary.get("dispatch", {}).get("policy", "none"), "無調度 baseline")
    lines = [
        f"# Real System Report — {policy_name}",
        "",
        "## 設定",
        "",
        f"- 調度政策：{policy_name}",
        f"- profile：{summary['profile']}",
        f"- 模擬時間：{summary['simulation_minutes']:.1f} 分鐘。",
        f"- 站點數：{summary['station_count']}",
        f"- 行政區數：{summary['district_count']}",
        f"- capacity / initial bikes 來源：{capacity_source}。",
        f"- capacity 範圍：{capacity_stats.get('min')} ~ {capacity_stats.get('max')}，"
        f"平均 {capacity_stats.get('mean')} 格。",
        f"- 旅行時間：機率化（道路距離 = 直線 × 繞路係數~N({summary['travel_time']['circuity_mean']}, "
        f"{summary['travel_time']['circuity_sd']})，車速~N({summary['travel_time']['speed_mean_kmph']}, "
        f"{summary['travel_time']['speed_sd_kmph']}) km/hr）。",
        "",
        "## 事件統計",
        "",
        f"- rider arrival：{summary['observed_total_arrivals']}",
        f"- successful routes：{summary['successful_routes']}",
        f"- shortage：{summary['shortage_events']}",
        f"- full station：{summary['full_station_events']}",
        f"- route planner fallback：{summary['route_planner_fallback_events']}",
    ]
    lines += metric_lines
    note_lines = [
        "",
        "## 注意",
        "",
        "- 本次使用 clean arrival rates 與 clean transition matrices。",
        "- `臺大專區` 已依清理規則合併到 `臺大公館校區`。",
    ]
    if summary["simulation_minutes"] < 1440:
        note_lines.append("- 此為短時長測試；優良時段占比需 24 小時全日模擬才有意義。")
    note_lines.append(
        "- 評估基準：真實 true standard（優良時段占比 ~60.7%）。任何調度政策須達/超越此線才算不輸現實。"
    )
    lines += note_lines
    report_dir.joinpath("report.md").write_text("\n".join(lines), encoding="utf-8")


# ---- 連續多日：午夜區間調度（虛擬結算法，§43；模擬內事件、守恆、直接搬活 container）----
_MD_MT_CAP, _MD_MT_SPEED, _MD_MT_WINDOW = 100, 30.0, 240.0   # 母車容量/車速/夜窗(00:00-04:00)
_MD_C_LABOR_NIGHT, _MD_C_KM, _MD_C_TRIP = 400.0, 8.0, 50.0   # 夜班費率/里程/出車（§42.5）


def _md_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    ml = math.radians((a[0] + b[0]) / 2.0)
    return math.hypot((b[1] - a[1]) * 111320.0 * math.cos(ml), (b[0] - a[0]) * 110540.0) / 1000.0


def overnight_settlement_process(env, stations, depots, s2d, target_d, station_target, centers, days, cost_log, mode="virtual"):
    """連續多日「午夜區間調度」事件（§43）。

    mode="virtual"（可部署，虛擬結算法）：每個午夜 k*1440 算各區「站總+場存」vs target_d；
      盈餘區從過滿站點回收車→赤字區集散場（白天 P7 再分到站）；貪婪最近對；計母車成本。
    mode="full"（絕對上界參考，免費）：每晚把**各站直接還原到 04:00 目標分布**（盡量，受可用車限制）；
      跳過集散場/最後一哩瓶頸，量「完美整備」天花板。
    **全程搬活的 simpy container（站 bikes/docks、場 inventory），守恆、零漏車。**
    """
    sids_by_d: dict[str, list] = {}
    for sid in stations:
        sids_by_d.setdefault(s2d.get(sid), []).append(sid)
    for k in range(1, days):
        yield env.timeout(k * 1440 - env.now)
        if mode == "full":
            # 絕對上界：把站點還原到 04:00 目標（守恆：先回收過量+集散場→pool，再補不足）。
            pool = 0
            for sid, st in stations.items():
                tgt = int(station_target.get(sid, st.available_bikes))
                if st.available_bikes > tgt:
                    take = st.available_bikes - tgt
                    yield st.bikes.get(take)
                    yield st.docks.put(take)
                    pool += take
            for dep in depots.values():
                pool += dep.inventory
                dep.inventory = 0
            for sid, st in stations.items():
                if pool <= 0:
                    break
                tgt = int(station_target.get(sid, st.available_bikes))
                add = min(tgt - st.available_bikes, pool, st.capacity - st.available_bikes)
                if add > 0:
                    yield st.docks.get(add)
                    yield st.bikes.put(add)
                    pool -= add
            if pool > 0 and depots:
                next(iter(depots.values())).inventory += pool   # 剩餘回任一集散場（守恆）
            cost_log.append({"day": k, "bikes_moved": 0, "km": 0.0, "loads": 0, "fleet": 0, "cost": 0.0})
            continue
        # 1) 盤點各區失衡（站總 + 場存 vs 目標）
        st_tot: dict[str, float] = {}
        for sid, station in stations.items():
            d = s2d.get(sid)
            if d:
                st_tot[d] = st_tot.get(d, 0.0) + station.available_bikes
        delta = {}
        for d in target_d:
            total = st_tot.get(d, 0.0) + (depots[d].inventory if d in depots else 0)
            delta[d] = target_d[d] - total
        S = [[d, -v] for d, v in delta.items() if v < -0.5 and d in centers]            # 盈餘→回收
        D = [[d, v] for d, v in delta.items() if v > 0.5 and d in centers and d in depots]  # 赤字→集散場
        # 2) 貪婪最近對配對（組員 Policy 2）
        legs = []
        while sum(v for _, v in S) > 0.5 and sum(v for _, v in D) > 0.5:
            best = None
            for i, (sd, sv) in enumerate(S):
                if sv <= 0.5:
                    continue
                for j, (dd, dv) in enumerate(D):
                    if dv <= 0.5:
                        continue
                    dist = _md_km(centers[sd], centers[dd])
                    if best is None or dist < best[0]:
                        best = (dist, i, j)
            if best is None:
                break
            dist, i, j = best
            flow = min(S[i][1], D[j][1])
            legs.append((S[i][0], D[j][0], flow, dist))
            S[i][1] -= flow
            D[j][1] -= flow
        # 3) 執行：從盈餘區過滿站回收 → 赤字區集散場（守恆搬運）
        total_km = total_handle = 0.0
        total_loads = moved = 0
        leg_recs: list[dict] = []          # 視覺化：每條母車（盈餘區→赤字區）
        depot_after: dict[str, int] = {}   # 視覺化：午夜結算後各赤字區集散場庫存
        for sd, dd, flow, dist in legs:
            need = int(round(flow))
            recovered = 0
            order = sorted(sids_by_d.get(sd, []),
                           key=lambda s: -(stations[s].available_bikes - station_target.get(s, stations[s].available_bikes)))
            for s in order:
                if recovered >= need:
                    break
                st = stations[s]
                excess = st.available_bikes - station_target.get(s, st.available_bikes)
                take = int(min(need - recovered, max(0, excess)))
                if take <= 0:
                    continue
                yield st.bikes.get(take)      # 站點移走（守恆：bikes↓ docks↑）
                yield st.docks.put(take)
                recovered += take
            if recovered > 0:
                depots[dd].inventory += recovered   # 入赤字區集散場（無容量上限，守恆）
                moved += recovered
                loads = max(1, math.ceil(recovered / _MD_MT_CAP))
                total_loads += loads
                total_km += loads * 2 * dist
                total_handle += 2.0 * (loads * 3 + 0.5 * recovered)   # 取+卸兩訪
                leg_recs.append({"from_d": sd, "to_d": dd, "bikes": int(recovered),
                                 "dist_km": round(dist, 3)})
                depot_after[dd] = int(depots[dd].inventory)
        drive = total_km / _MD_MT_SPEED * 60.0
        work = total_handle + drive
        fleet = max(1, math.ceil(work / _MD_MT_WINDOW)) if work > 0 else 0
        labor = fleet * (_MD_MT_WINDOW / 60.0) * _MD_C_LABOR_NIGHT
        cost = round(labor + _MD_C_KM * total_km + _MD_C_TRIP * total_loads, 1)
        cost_log.append({"day": k, "bikes_moved": moved, "km": round(total_km, 1),
                         "loads": total_loads, "fleet": fleet, "cost": cost,
                         "legs": leg_recs, "depot_after": depot_after})  # 後兩者供視覺化母車


def build_mother_truck_legs(md_cost_log, depot_xy, centers_xy, mt_cap):
    """把午夜結算記錄轉成視覺化用的母車軌跡 + 集散場庫存午夜階梯（§43 視覺化）。

    每條母車 = 一條「盈餘區中心 → 赤字區集散場」運程，行駛時間攤在夜窗（00:00→04:00）內，
    至少 20 分鐘以利觀看；抵達後停泊赤字區集散場（短停留段，之後動畫不再畫在路上）。
    回傳 (legs, depot_events)；legs 直接 extend 進 dispatch_system.truck_legs（帶 kind="mother" 與 fromXY/toXY）。
    """
    def xy_of(d):
        if d in depot_xy:
            return depot_xy[d]["x"], depot_xy[d]["y"]
        if d in centers_xy:
            return centers_xy[d]
        return None

    legs: list[dict] = []
    depot_events: list[dict] = []
    for entry in md_cost_log:
        k = entry.get("day")
        base = float(k) * 1440.0
        window_end = base + _MD_MT_WINDOW          # 夜窗結束（04:00）＝母車收工、駐留標示清除
        recs = entry.get("legs") or []
        arrivals: dict[str, list[tuple[float, int]]] = {}   # 各赤字區集散場：[(到達時間, 載車數)]
        for i, r in enumerate(recs):
            a = xy_of(r["from_d"])
            b = xy_of(r["to_d"])
            if not a or not b:
                continue
            drive = max(20.0, r["dist_km"] / _MD_MT_SPEED * 60.0)   # 至少 20 分可看
            t0 = base + 10.0 + i * 8.0                              # 夜窗內錯位起程
            if t0 + drive > window_end:
                span = max(1.0, _MD_MT_WINDOW - drive - 10.0)
                t0 = base + 10.0 + ((i * 8.0) % span)
            t1 = t0 + drive
            tid = f"M{k}_{i}"
            # 1) 行駛段：盈餘區中心 → 赤字區集散場
            legs.append({"truckId": tid, "district": r["to_d"], "kind": "mother",
                         "t0": round(t0, 4), "t1": round(t1, 4),
                         "fromType": "center", "fromId": r["from_d"],
                         "toType": "depot", "toId": r["to_d"],
                         "fromXY": {"x": a[0], "y": a[1]}, "toXY": {"x": b[0], "y": b[1]},
                         "load": int(r["bikes"]), "cap": int(mt_cap), "moving": True})
            # 2) 駐留段：抵達後停在赤字區集散場直到夜窗結束（動畫不畫在路上；集散場以「母」標示）
            legs.append({"truckId": tid, "district": r["to_d"], "kind": "mother",
                         "t0": round(t1, 4), "t1": round(max(window_end, t1 + 15.0), 4),
                         "fromType": "depot", "fromId": r["to_d"],
                         "toType": "depot", "toId": r["to_d"],
                         "toXY": {"x": b[0], "y": b[1]},
                         "load": int(r["bikes"]), "cap": int(mt_cap), "moving": False})
            arrivals.setdefault(r["to_d"], []).append((t1, int(r["bikes"])))
        # 集散場庫存：午夜先維持「結算前」值，再隨每台母車「抵達時」逐台累加（修正：非午夜一次加）。
        depot_after = entry.get("depot_after") or {}
        for dd, arrs in arrivals.items():
            final_inv = depot_after.get(dd)
            if final_inv is None:
                continue
            pre = int(final_inv) - sum(b for _, b in arrs)
            depot_events.append({"depot": dd, "time": round(base, 4), "inventory": int(pre)})
            running = pre
            for t1, bikes in sorted(arrs):
                running += bikes
                depot_events.append({"depot": dd, "time": round(t1, 4), "inventory": int(running)})
    return legs, depot_events


def run_real_system_scenario(
    hours: float,
    seed: int,
    profile: str,
    label: str,
    output_root: Path,
    station_capacity: int,
    initial_bikes: int,
    speed_kmph: float,
    min_trip_minutes: float,
    arrival_dir: Path,
    transition_dir: Path,
    visualization_input_dir: Path,
    capacity_json: Path,
    circuity_mean: float,
    circuity_sd: float,
    speed_sd_kmph: float,
    rental_lognorm_mu: float,
    rental_lognorm_sigma: float,
    rental_cap_minutes: float,
    self_station_ratio: float | None,
    self_lognorm_mu: float,
    self_lognorm_sigma: float,
    excellent_benchmark_csv: Path,
    dispatch_policy: str = "none",
    trucks_per_district: "int | dict[str, int]" = 4,
    truck_capacity: int = 30,
    truck_initial_load: int = 15,
    truck_speed_kmph: float = 50.0,
    truck_speed_sd_kmph: float = 10.0,
    depot_capacity: int = 300,
    depot_initial: int = 300,
    cost_per_labor_hour: float = 300.0,
    cost_per_km: float = 8.0,
    cost_per_trip: float = 50.0,
    cost_per_truck_fixed: float = 0.0,
    min_action_ratio: float = 0.15,
    demand_weight_alpha: float = 0.0,
    forecast_base_ratio: float = 0.65,
    forecast_horizon_hours: float = 3.0,
    preposition_minutes: tuple[float, ...] = (330.0, 930.0),
    patrol_starts: tuple[float, ...] = (360.0, 960.0),
    patrol_duration: float = 90.0,
    report_subdir: str = "",
    duty_windows: tuple[tuple[float, float], ...] | None = None,
    total_fleet: int = 26156,
    maintenance_fraction: float = 0.15,
    depot_distribution: str = "demand",
    depot_headroom: float = 1.5,
    start_minute: float = 0.0,
    snapshot_csv: Path | None = None,
    config_override: dict[str, float] | None = None,
    variant_label: str = "",
    truck_allocation_mode: str = "uniform",
    total_trucks: int = 0,
    truck_allocation_custom: dict[str, int] | None = None,
    depot_init_override: dict[str, int] | None = None,
    days: int = 1,
    overnight_mode: str = "none",
) -> Path:
    """執行真實系統 scenario（可選調度），並輸出數據結果分析報告。

    config_override：覆寫 DispatchConfig 任意數值欄位（grid search 用，如 {'target_high_ratio':0.8}）。
    variant_label：此 run 的變體標籤，寫入 summary['dispatch']['variant_label']，供 Pareto 圖區分。

    start_minute：模擬起點分鐘（06:00 快照工作流設 360）。env 從此刻起跑，hours 為
    模擬「時長」，故結束於 start_minute + hours×60。見 architecture_narrative §32。
    snapshot_csv：真實站點車輛快照，覆寫各站初始；集散場初始由守恆殘差自動算出。
    """

    # simulation_minutes 此處代表「絕對結束分鐘」（= 起點 + 時長），供 metrics 的
    # 絕對時刻窗（優良時段 06:00–24:00）與 env.run(until=...) 使用。
    # 連續多日（§43，days>1）：跑到第 days 天的 24:00（= days×1440），中途午夜做區間調度事件。
    simulation_minutes = float(days * 1440) if days > 1 else start_minute + hours * 60.0
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    hours_tag = f"{int(round(hours))}h" if hours >= 1 else f"{int(round(hours * 60))}min"
    # 人性化資料夾名稱：一眼看出 label（測試內容）、profile、時長、seed。
    # report_subdir 讓不同實驗各自歸入子資料夾（避免 report/ 雜亂）。
    base_root = output_root / report_subdir if report_subdir else output_root
    report_dir = base_root / (
        f"real_system_{label}_{profile}_{hours_tag}_seed{seed}_{timestamp}"
    )
    report_dir.mkdir(parents=True, exist_ok=False)

    station_positions = load_json(visualization_input_dir / "station_positions.json")
    rent_lambda_by_station = load_hourly_lambda_by_station(
        arrival_dir / "hourly_rent_lambda_by_station.json"
    )

    # 同站來回比例：未指定時，從該 profile 的 OD build metadata 讀真實 self_station_ratio。
    if self_station_ratio is None:
        metadata_path = transition_dir / f"{profile}_build_metadata.json"
        self_station_ratio = (
            float(load_json(metadata_path).get("self_station_ratio", 0.0))
            if metadata_path.exists()
            else 0.0
        )

    # 真實 capacity / initial bikes / benchmark（檔案不存在則退回 CLI 假設值）。
    capacity_data = load_station_capacity(capacity_json)
    uses_real_capacity = bool(capacity_data)
    capacity_by_station = {
        sid: int(info["capacity"]) for sid, info in capacity_data.items()
    }
    initial_by_station = {
        sid: int(info["initial_bikes"]) for sid, info in capacity_data.items()
    }

    # 06:00 真實快照覆寫各站初始（見 §32）。集散場初始由 build_depot_config 的守恆
    # 殘差（車隊 − Σ站點 − 維修）自動重算，不需另外處理。
    if snapshot_csv is not None:
        snapshot = load_station_snapshot(snapshot_csv)
        # 只覆寫「本系統有的站」（快照可能多出幾站，跳過以保守恆精確）；
        # 快照沒有的本系統站維持原值（資料缺漏，不動）。
        known = set(station_positions.keys())
        applied = skipped = 0
        for sid, bikes in snapshot.items():
            if sid not in known:
                skipped += 1
                continue
            cap = capacity_by_station.get(sid)
            initial_by_station[sid] = min(bikes, cap) if cap else bikes
            applied += 1
        print(
            f"[snapshot] 套用 {applied} 站（跳過 {skipped} 站不在本系統）；起跑 "
            f"{start_minute/60:.1f}:00、站點初始總和 {sum(initial_by_station.values())}。",
            flush=True,
        )

    env = simpy.Environment(initial_time=start_minute)
    rng = random.Random(seed)
    model, stations = build_real_model(
        env,
        rng,
        station_positions,
        transition_dir,
        profile,
        capacity_by_station,
        initial_by_station,
        station_capacity,
        initial_bikes,
        speed_kmph,
        min_trip_minutes,
        circuity_mean,
        circuity_sd,
        speed_sd_kmph,
        rental_lognorm_mu,
        rental_lognorm_sigma,
        rental_cap_minutes,
        self_station_ratio,
        self_lognorm_mu,
        self_lognorm_sigma,
    )

    # 補上沒有 capacity 檔的站點（用 CLI 假設值），供 metrics 使用。
    for station in stations:
        capacity_by_station.setdefault(station.station_id, station_capacity)
        initial_by_station.setdefault(station.station_id, initial_bikes)

    for station in stations:
        station_lambda = rent_lambda_by_station.get(station.station_id)
        if station_lambda is None:
            continue
        env.process(demand_generator(env, station, station_lambda))

    # 調度系統（policy="none" 時不掛卡車，即無調度 baseline）。
    dispatch_config = DispatchConfig(
        truck_capacity=truck_capacity,
        truck_initial_load=truck_initial_load,
        truck_speed_kmph=truck_speed_kmph,
        truck_speed_sd_kmph=truck_speed_sd_kmph,
        depot_capacity=depot_capacity,
        depot_initial=depot_initial,
        cost_per_labor_hour=cost_per_labor_hour,
        cost_per_km=cost_per_km,
        cost_per_trip=cost_per_trip,
        cost_per_truck_fixed=cost_per_truck_fixed,
        min_action_ratio=min_action_ratio,
        demand_weight_alpha=demand_weight_alpha,
        forecast_base_ratio=forecast_base_ratio,
        forecast_horizon_hours=forecast_horizon_hours,
        preposition_minutes=tuple(preposition_minutes),
        duty_windows=duty_windows,
        patrol_start_minutes=tuple(patrol_starts),
        patrol_duration_minutes=patrol_duration,
    )
    # grid search：覆寫 DispatchConfig 任意數值欄位（如 target_high_ratio、ucl_ratio、
    # score_distance_const_km_dynamic、dynamic_scan_minutes…）。見 §38。
    if config_override:
        for key, val in config_override.items():
            if not hasattr(dispatch_config, key):
                raise ValueError(f"未知的 DispatchConfig 欄位：{key}")
            setattr(dispatch_config, key, type(getattr(dispatch_config, key))(val))
    # 逐區集散場初始/容量（三桶守恆，依需求或容量分配；見 architecture_narrative §27）。
    depot_cfg = build_depot_config(
        station_positions,
        capacity_by_station,
        initial_by_station,
        rent_lambda_by_station,
        total_fleet,
        maintenance_fraction,
        depot_distribution,
        depot_headroom,
    )
    # 逐區集散場初始覆寫（多日 Model B：夜間整備把車送到「集散場」，見 §42.5）。
    # 守恆由呼叫端（run_multiday）保證——夜間 station→depot 為守恆搬運。
    # 容量取 max(原容量, 覆寫值×headroom)，讓集散場既能存儲備、也能白天接收抽回的車。
    if depot_init_override:
        for d, init in depot_init_override.items():
            if d not in depot_cfg:
                continue
            init = max(0, int(round(init)))
            cap = max(depot_cfg[d]["capacity"], int(round(init * depot_headroom)), init)
            depot_cfg[d] = {"initial": init, "capacity": cap}
        print(f"[depot-override] 套用逐區集散場初始（總 {sum(int(v) for v in depot_init_override.values())} 台，{len(depot_init_override)} 區）。", flush=True)
    # P5 需求加權用的站點日需求（總λ）。
    station_demand = {
        sid: sum(float(x) for x in (hl.values() if isinstance(hl, dict) else hl))
        for sid, hl in rent_lambda_by_station.items()
    }
    # per-district 逐區配車（§39）：非 uniform 時依基準/自訂把 total 台車分配到各區。
    if truck_allocation_mode in ("stations", "demand"):
        trucks_per_district = allocate_trucks(
            station_positions, rent_lambda_by_station, int(total_trucks), truck_allocation_mode
        )
        print(f"[allocation] {truck_allocation_mode} total={total_trucks} → {trucks_per_district}", flush=True)
    elif truck_allocation_mode == "custom" and truck_allocation_custom:
        trucks_per_district = dict(truck_allocation_custom)
        print(f"[allocation] custom total={sum(trucks_per_district.values())} → {trucks_per_district}", flush=True)
    dispatch_system = attach_dispatch(
        env,
        model,
        station_positions,
        policy=dispatch_policy,
        trucks_per_district=trucks_per_district,
        config=dispatch_config,
        rng=rng,
        depot_config=depot_cfg,
        station_demand=station_demand,
        station_hourly_demand=rent_lambda_by_station,
    )

    # 連續多日（§43）：掛上「午夜區間調度」事件（虛擬結算法；none 不掛）。
    md_cost_log: list[dict] = []
    if days > 1 and overnight_mode in ("virtual", "full"):
        s2d_md = {sid: pos["district"] for sid, pos in station_positions.items()}
        target_d_md: dict[str, float] = {}
        for sid, b in initial_by_station.items():
            d = s2d_md.get(sid)
            if d:
                target_d_md[d] = target_d_md.get(d, 0.0) + b
        pts_md: dict[str, list] = {}
        for sid, pos in station_positions.items():
            pts_md.setdefault(pos["district"], []).append((pos["latitude"], pos["longitude"]))
        centers_md = {d: (sum(a for a, _ in q) / len(q), sum(b for _, b in q) / len(q)) for d, q in pts_md.items()}
        env.process(overnight_settlement_process(
            env, model.stations, dispatch_system.depots, s2d_md, target_d_md,
            initial_by_station, centers_md, days, md_cost_log, overnight_mode))

    env.run(until=simulation_minutes)

    summary = summarize_events(
        model,
        simulation_minutes,
        uses_real_capacity,
        speed_kmph,
        profile,
    )

    metrics = compute_run_metrics(
        event_log=model.event_log,
        snapshots=summary["station_snapshots"],
        capacity_by_station=capacity_by_station,
        initial_by_station=initial_by_station,
        station_positions=station_positions,
        benchmark_by_station=capacity_data,
        simulation_minutes=simulation_minutes,
        excellent_benchmark=load_excellent_benchmark(excellent_benchmark_csv, profile),
        window_start=start_minute,
    )
    summary["metrics"] = metrics
    # 連續多日（§43）：逐日優良占比 + 夜間區間調度成本 + 守恆檢查（站+場總車量逐日不漏）。
    if days > 1:
        pde = per_day_excellent(model.event_log, capacity_by_station, initial_by_station, days)
        pdsl = per_day_service_level(model.event_log, days)
        bikes_total = sum(s.available_bikes for s in model.stations.values()) + sum(
            d.inventory for d in dispatch_system.depots.values())
        summary["multiday"] = {
            "days": days,
            "overnight_mode": overnight_mode,
            "per_day_excellent": pde,
            "per_day_service_level": pdsl,
            "night_cost_log": md_cost_log,
            "night_cost_total": round(sum(c["cost"] for c in md_cost_log), 1),
            "bikes_in_system_end": bikes_total,  # 站+場期末總車（守恆觀察）
        }
    summary["arrival_rate_source"] = arrival_dir.name
    summary["travel_time"] = {
        "model": "probabilistic",
        "circuity_mean": circuity_mean,
        "circuity_sd": circuity_sd,
        "speed_mean_kmph": speed_kmph,
        "speed_sd_kmph": speed_sd_kmph,
        "occupancy": "max(ride, lognormal)",
        "rental_lognorm_mu": rental_lognorm_mu,
        "rental_lognorm_sigma": rental_lognorm_sigma,
        "rental_cap_minutes": rental_cap_minutes,
        "self_station_ratio": self_station_ratio,
        "self_lognorm_mu": self_lognorm_mu,
        "self_lognorm_sigma": self_lognorm_sigma,
    }

    # ---- 調度成本 + SC Ratio（提案 p.13 主 KPM）----
    event_counts = summary["event_counts"]
    shortage = int(summary["shortage_events"])
    full = int(summary["full_station_events"])
    rentals = int(event_counts.get("rental", 0))
    returns = int(event_counts.get("return", 0))
    rent_attempts = shortage + rentals          # 借車側互動總數
    return_attempts = full + returns            # 還車側互動總數
    denom = rent_attempts + return_attempts
    service_level = 1.0 - (shortage + full) / denom if denom else 0.0
    cost_breakdown = dispatch_system.cost_breakdown()
    dispatching_cost = cost_breakdown["total"]
    sc_ratio = service_level / dispatching_cost if dispatching_cost > 0 else None
    summary["dispatch"] = {
        "policy": dispatch_policy,
        "variant_label": variant_label,
        "config_override": dict(config_override) if config_override else {},
        "trucks_per_district": (
            (sum(trucks_per_district.values()) if isinstance(trucks_per_district, dict)
             else trucks_per_district) if dispatch_policy != "none" else 0
        ),
        "truck_allocation": dict(trucks_per_district) if isinstance(trucks_per_district, dict) else None,
        "truck_count": dispatch_system.truck_count,
        "config": {
            "truck_capacity": truck_capacity,
            "truck_initial_load": truck_initial_load,
            "truck_speed_kmph": truck_speed_kmph,
            "truck_speed_sd_kmph": truck_speed_sd_kmph,
            "depot_capacity": depot_capacity,
            "depot_initial": depot_initial,
            "cost_per_labor_hour": cost_per_labor_hour,
            "cost_per_km": cost_per_km,
            "cost_per_trip": cost_per_trip,
            "cost_per_truck_fixed": cost_per_truck_fixed,
            "min_action_ratio": min_action_ratio,
            "demand_weight_alpha": demand_weight_alpha,
            "duty_windows": [list(w) for w in duty_windows] if duty_windows else "full(06-24)",
            "patrol_starts": list(patrol_starts),
            "patrol_duration": patrol_duration,
            "total_fleet": total_fleet,
            "maintenance_fraction": maintenance_fraction,
            "depot_distribution": depot_distribution,
            "depot_headroom": depot_headroom,
            "depot_config_by_district": depot_cfg,
        },
        "stats": dispatch_system.stats.as_dict(),
        "cost_breakdown": cost_breakdown,
        "depot_inventory_end": {
            d: dep.inventory for d, dep in sorted(dispatch_system.depots.items())
        },
        # 各區集散場全程時間加權平均庫存（觀察項；集散場影響成本、非品質標準）。
        "depot_avg_bikes": depot_avg_inventory(
            dispatch_system.depot_timeline, simulation_minutes, start_minute
        ),
        # 各區調度成本分項（供成本佔比、各區 SC ratio）。
        "district_cost": dispatch_system.district_cost_breakdown(),
    }
    summary["sc_ratio_block"] = {
        "service_level": round(service_level, 5),
        "service_level_definition": "1 - (shortages + full) / (rent_attempts + return_attempts)",
        "rent_attempts": rent_attempts,
        "return_attempts": return_attempts,
        "dispatching_cost": round(dispatching_cost, 4),
        "dispatching_cost_definition": "C_labor*工時 + C_km*里程 + C_trip*出車 + C_fix*車隊",
        "cost_breakdown": cost_breakdown,
        "sc_ratio": round(sc_ratio, 9) if sc_ratio is not None else None,
        # 成本數值大→SC Ratio 很小；另存 ×1e6 方便閱讀比較。
        "sc_ratio_per_million": round(sc_ratio * 1e6, 4) if sc_ratio is not None else None,
    }

    # 敘述放在 dispatch/sc 算完之後，才能帶政策、成本與 true-standard 判定。
    narrative = build_narrative(
        metrics, profile, summary["dispatch"], summary["sc_ratio_block"]
    )

    report_dir.joinpath("summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(report_dir / "events.csv", model.event_log)
    write_csv(report_dir / "station_snapshots.csv", summary["station_snapshots"])
    write_markdown_report(
        report_dir,
        summary,
        format_report_lines(metrics, narrative, summary["dispatch"], summary["sc_ratio_block"]),
    )

    # 連續多日 virtual：把午夜母車（跨區結算）併入卡車軌跡與集散場庫存階梯，動畫才看得到母車。
    if days > 1 and overnight_mode == "virtual" and md_cost_log:
        centers_xy: dict[str, tuple[float, float]] = {}
        _acc: dict[str, list] = {}
        for sid, pos in station_positions.items():
            if "x" in pos and "y" in pos:
                _acc.setdefault(pos["district"], []).append((float(pos["x"]), float(pos["y"])))
        for d, q in _acc.items():
            centers_xy[d] = (sum(x for x, _ in q) / len(q), sum(y for _, y in q) / len(q))
        m_legs, m_depot = build_mother_truck_legs(
            md_cost_log, dispatch_system.depot_xy, centers_xy, _MD_MT_CAP)
        dispatch_system.truck_legs.extend(m_legs)
        dispatch_system.depot_timeline.extend(m_depot)

    # 輸出調度軌跡（集散站 + 卡車隨時間移動 + 集散站庫存時間線），供視覺化動畫。
    dispatch_trajectory = {
        "policy": dispatch_policy,
        "truckCapacity": truck_capacity,
        "depots": [
            {
                "district": did,
                "latitude": dep.latitude,
                "longitude": dep.longitude,
                "x": dispatch_system.depot_xy.get(did, {}).get("x"),
                "y": dispatch_system.depot_xy.get(did, {}).get("y"),
                "capacity": dep.capacity,
                "initial": depot_initial,
            }
            for did, dep in sorted(dispatch_system.depots.items())
        ],
        "depotTimeline": dispatch_system.depot_timeline,
        "truckLegs": dispatch_system.truck_legs,
    }
    report_dir.joinpath("dispatch_trajectory.json").write_text(
        json.dumps(dispatch_trajectory, ensure_ascii=False),
        encoding="utf-8",
    )

    # 輸出每小時健康站比例軌跡圖（time-to-fail 視覺化）。
    policy_label = {
        "none": "無調度",
        "fixed": "P1 固定巡迴",
        "dynamic": "P2 動態觸發",
        "hybrid_anticipatory": "P3 預置+反應",
        "hybrid_smartshift": "P4 智慧班次",
        "hybrid_forecast": "P6 預測式預置",
        "pair_coord": "P7 配對協調",
        "optimal_ub": "最佳化上界",
    }.get(dispatch_policy, dispatch_policy)
    write_trajectory_plot(report_dir, metrics, policy_label)

    # 直接把結果報告印到 console。
    print(format_console(metrics, narrative))
    print(format_dispatch_console(summary))
    return report_dir


def format_dispatch_console(summary: dict[str, Any]) -> str:
    """格式化調度成本 + SC Ratio 區塊（每次跑都印）。"""

    d = summary.get("dispatch", {})
    sc = summary.get("sc_ratio_block", {})
    lines = ["", "===== 調度 / SC Ratio =====",
             f"政策：{d.get('policy', 'none')}　調度車數：{d.get('truck_count', 0)}"
             f"（每區 {d.get('trucks_per_district', 0)}）"]
    stats = d.get("stats", {})
    if d.get("policy") not in ("none", None):
        lines.append(
            f"出車次數：{stats.get('trips', 0)}　總里程：{stats.get('total_km', 0)} km　"
            f"搬運車輛：{stats.get('bikes_moved', 0)}"
            f"（補 {stats.get('bikes_replenished', 0)} / 抽 {stats.get('bikes_withdrawn', 0)}）"
        )
        lines.append(
            f"到站搬運：{stats.get('station_visits', 0)} 次　白跑：{stats.get('wasted_visits', 0)} 次　"
            f"計薪工時：{stats.get('on_duty_hours', 0)} hr　休息：{stats.get('rest_minutes', 0)} min"
        )
        cb = d.get("cost_breakdown", {})
        lines.append(
            f"成本分項(NT$)：人力 {cb.get('labor', 0):.0f}　里程 {cb.get('mileage', 0):.0f}　"
            f"出車 {cb.get('trip', 0):.0f}　固定 {cb.get('fixed', 0):.0f}"
        )
    lines.append(
        f"ServiceLevel：{sc.get('service_level')}　"
        f"DispatchingCost：{sc.get('dispatching_cost')}　"
        f"SC Ratio：{sc.get('sc_ratio')}"
    )
    return "\n".join(lines)


def write_trajectory_plot(
    report_dir: Path, metrics: dict[str, Any], policy_label: str = "無調度"
) -> None:
    """畫出每小時「健康站比例」與「平均填充率」軌跡，並標出真實 benchmark 與 time-to-fail。"""

    traj = metrics.get("hourly_trajectory") or []
    if not traj:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    hours = [p["hour"] for p in traj]
    healthy = [p["healthy_fraction"] * 100 for p in traj]
    fill = [p["mean_fill"] * 100 for p in traj]
    excellent = metrics.get("excellent") or {}
    bench = (excellent.get("benchmark") or {}).get("mean")

    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.plot(hours, healthy, "-o", color="#2f78b5", label="健康站比例 (20-80%)")
    ax.plot(hours, fill, "-s", color="#7f5fa0", label="系統平均填充率", alpha=0.7)
    if bench is not None:
        ax.axhline(bench * 100, color="#4d8f5f", ls="--", label=f"真實 benchmark {bench * 100:.0f}%")
    ttf = metrics.get("time_to_fail_hour")
    if ttf is not None:
        ax.axvline(ttf, color="#c42f2f", ls=":", label=f"time-to-fail {int(ttf):02d}:00")
    ax.axvspan(0, 6, color="#eef1f4", alpha=0.6)  # 夜間（不計入指標窗）
    ax.set_xlabel("小時 (0-24)")
    ax.set_ylabel("百分比 (%)")
    ax.set_ylim(0, 100)
    ax.set_xticks(range(0, 25, 2))
    ax.set_title(f"系統健康站比例隨時間變化（{policy_label}）")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(report_dir / "hourly_trajectory.png", dpi=110)
    plt.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real-system YouBike baseline.")
    parser.add_argument("--hours", type=float, default=2.0, help="simulation hours")
    parser.add_argument("--seed", type=int, default=20260528, help="random seed")
    parser.add_argument(
        "--profile",
        choices=["weekday", "weekend"],
        default="weekday",
        help="transition matrix profile",
    )
    parser.add_argument(
        "--label",
        type=str,
        default="baseline",
        help="可讀的測試標籤，會放進報告資料夾名稱（例如 baseline、nodispatch、policyA）。",
    )
    parser.add_argument(
        "--station-capacity",
        type=int,
        default=30,
        help="fallback capacity for stations missing from the capacity file",
    )
    parser.add_argument(
        "--initial-bikes",
        type=int,
        default=15,
        help="fallback initial bikes for stations missing from the capacity file",
    )
    parser.add_argument(
        "--capacity-json",
        type=Path,
        default=PROJECT_ROOT / "data/processed/station_capacity/station_capacity.json",
        help="per-station real capacity / initial bikes / benchmark",
    )
    parser.add_argument(
        "--speed-kmph",
        type=float,
        default=DEFAULT_SPEED_MEAN_KMPH,
        help="車速高斯分布的均值（km/hr）。",
    )
    parser.add_argument(
        "--speed-sd-kmph",
        type=float,
        default=DEFAULT_SPEED_SD_KMPH,
        help="車速高斯分布的標準差（km/hr）。",
    )
    parser.add_argument(
        "--circuity-mean",
        type=float,
        default=DEFAULT_CIRCUITY_MEAN,
        help="繞路係數（道路/直線距離）高斯分布的均值。",
    )
    parser.add_argument(
        "--circuity-sd",
        type=float,
        default=DEFAULT_CIRCUITY_SD,
        help="繞路係數高斯分布的標準差。",
    )
    parser.add_argument(
        "--rental-lognorm-mu",
        type=float,
        default=DEFAULT_RENTAL_LOGNORM_MU,
        help="租借時長（佔用時間）對數常態的 mu。",
    )
    parser.add_argument(
        "--rental-lognorm-sigma",
        type=float,
        default=DEFAULT_RENTAL_LOGNORM_SIGMA,
        help="租借時長（佔用時間）對數常態的 sigma。",
    )
    parser.add_argument(
        "--rental-cap-minutes",
        type=float,
        default=DEFAULT_RENTAL_CAP_MINUTES,
        help="佔用時間上限（分鐘）。",
    )
    parser.add_argument(
        "--self-station-ratio",
        type=float,
        default=None,
        help="同站來回比例；未指定時用該 profile 的真實 self_station_ratio。",
    )
    parser.add_argument(
        "--self-lognorm-mu",
        type=float,
        default=DEFAULT_SELF_LOGNORM_MU,
        help="同站來回佔用時間對數常態的 mu（mean≈45 分）。",
    )
    parser.add_argument(
        "--self-lognorm-sigma",
        type=float,
        default=DEFAULT_SELF_LOGNORM_SIGMA,
        help="同站來回佔用時間對數常態的 sigma。",
    )
    parser.add_argument(
        "--excellent-benchmark-csv",
        type=Path,
        default=PROJECT_ROOT
        / "data/benchmark/percentage_of_excellent/站點優良時段比例.csv",
        help="真實「優良時段占比」benchmark CSV，用於對照。",
    )
    parser.add_argument("--min-trip-minutes", type=float, default=2.0)
    parser.add_argument(
        "--arrival-dir",
        type=Path,
        default=None,
        help=(
            "arrival rate 目錄；預設依 profile 自動選 "
            "data/processed/arrival_rates_{profile}_clean。"
        ),
    )
    parser.add_argument(
        "--transition-dir",
        type=Path,
        default=PROJECT_ROOT / "data/processed/transition_matrices_clean",
    )
    parser.add_argument(
        "--visualization-input-dir",
        type=Path,
        default=PROJECT_ROOT / "data/processed/visualization_inputs",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "report",
    )
    # ---- 調度（rebalancing）參數 ----
    parser.add_argument(
        "--dispatch-policy",
        choices=["none", "fixed", "dynamic", "hybrid_anticipatory", "hybrid_smartshift", "hybrid_forecast", "pair_coord", "optimal_ub"],
        default="none",
        help=(
            "調度政策：none=無調度；fixed=P1 固定巡迴；dynamic=P2 動態觸發；"
            "hybrid_anticipatory=P3 排程預置+反應(10/90)；hybrid_smartshift=P4 排定班次+平方選站+提早收工。"
        ),
    )
    parser.add_argument(
        "--trucks-per-district",
        type=int,
        default=4,
        help="每行政區調度車數（預設 4 ≈ 全市 ~48 台；見 architecture_narrative §16.7）。",
    )
    parser.add_argument("--truck-capacity", type=int, default=30, help="調度車載量上限。")
    parser.add_argument("--truck-initial-load", type=int, default=15, help="出車起始載量。")
    parser.add_argument("--truck-speed-kmph", type=float, default=50.0, help="調度車車速高斯均值（km/hr）。")
    parser.add_argument("--truck-speed-sd-kmph", type=float, default=10.0, help="調度車車速高斯標準差。")
    parser.add_argument("--depot-capacity", type=int, default=300, help="集散場庫存上限（逐區覆寫時為 fallback）。")
    parser.add_argument("--depot-initial", type=int, default=300, help="集散場初始庫存（逐區覆寫時為 fallback）。")
    parser.add_argument("--total-fleet", type=int, default=26156, help="真實全市車隊數（守恆用，2026/4）。")
    parser.add_argument("--maintenance-fraction", type=float, default=0.15, help="維修/不可用車比例（文獻 ~15-17%）。")
    parser.add_argument("--depot-distribution", choices=["demand", "capacity"], default="demand", help="集散場初始逐區分配依據。")
    parser.add_argument("--depot-headroom", type=float, default=1.5, help="集散場容量上限 = 初始 × 此倍率。")
    parser.add_argument("--cost-per-labor-hour", type=float, default=300.0, help="人力每工時成本 C_labor（NT$）。")
    parser.add_argument("--cost-per-km", type=float, default=8.0, help="每公里成本 C_mileage（油+耗損，NT$）。")
    parser.add_argument("--cost-per-trip", type=float, default=50.0, help="每次出車雜支 C_trip（NT$）。")
    parser.add_argument("--cost-per-truck-fixed", type=float, default=0.0, help="每車隊固定/攤提 C_fix（預設0；同車隊相消）。")
    parser.add_argument("--min-action-ratio", type=float, default=0.15, help="站點『偏離目標/容量』≥ 此比率才值得服務（取代絕對台數門檻）。")
    parser.add_argument("--demand-weight-alpha", type=float, default=0.0, help="P5 需求加權選站：score×(日λ)^alpha。0=關，0.5=√λ。")
    parser.add_argument("--forecast-base-ratio", type=float, default=0.65, help="P6 預置基準水位（無預測流出時）。")
    parser.add_argument("--forecast-horizon-hours", type=float, default=3.0, help="P6 預測未來幾小時借走量。")
    parser.add_argument("--preposition-minutes", type=str, default="330,930", help="P6 預置時點（分鐘，逗號分隔；預設 05:30,15:30）。")
    parser.add_argument("--report-subdir", type=str, default="", help="報告子資料夾（每個實驗各自歸檔，例如 exp02_partial_standby）。")
    parser.add_argument(
        "--duty-windows",
        type=str,
        default="",
        help="多段值勤窗（分鐘，格式 '300-600,900-1200'）；空=用全日值勤窗 06:00-24:00。",
    )
    parser.add_argument(
        "--patrol-starts",
        type=str,
        default="330,930",
        help="排程/巡迴政策(P1/P3/P4)每日出車起點（分鐘，逗號分隔；預設 05:30,15:30＝峰前 1–1.5h，與 P6 預置對齊）。見 §34。",
    )
    parser.add_argument(
        "--patrol-duration",
        type=float,
        default=90.0,
        help="Policy 1 每班時長（分鐘，預設 90=1.5hr）。",
    )
    parser.add_argument(
        "--snapshot-csv",
        type=Path,
        default=None,
        help="真實站點車輛快照（CSV/JSON），覆寫各站初始；集散場初始由守恆殘差自動算。配 --start-minute 指定快照時刻。見 §32/§34。",
    )
    parser.add_argument(
        "--start-minute",
        type=float,
        default=-1.0,
        help="模擬起點分鐘（hours 為時長，結束於 起點+時長）。-1=自動：有快照→360(06:00)，否則 0。",
    )
    parser.add_argument(
        "--dispatch-config-override",
        type=str,
        default="",
        help="覆寫 DispatchConfig 數值欄位（grid search 用）；格式 'target_high_ratio=0.8,ucl_ratio=0.85'。",
    )
    parser.add_argument(
        "--variant-label",
        type=str,
        default="",
        help="此 run 變體標籤，寫入 summary 供 P7 參數 Pareto 圖區分（如 'tgt80/20'）。",
    )
    parser.add_argument(
        "--truck-allocation",
        choices=["uniform", "stations", "demand", "custom"],
        default="uniform",
        help="逐區配車（§39）：uniform=每區同數；stations/demand=總車 ∝站數/需求；custom=用 --truck-allocation-json。",
    )
    parser.add_argument("--total-trucks", type=int, default=0, help="非 uniform 配置的總車隊預算。")
    parser.add_argument(
        "--truck-allocation-json", type=str, default="",
        help="custom 配置的 {行政區:車數} JSON（如貪婪最優配置）。",
    )
    parser.add_argument(
        "--depot-init-json", type=str, default="",
        help="逐區集散場初始 {行政區:台數} JSON（多日 Model B 夜間整備到集散場；utf-8 檔或 inline）。",
    )
    parser.add_argument("--days", type=int, default=1, help="連續多日天數（§43）；>1 啟用連續模擬 + 午夜區間調度。")
    parser.add_argument("--overnight-mode", choices=["none", "virtual", "full"], default="none",
                        help="連續多日夜間區間調度：none=不調度、virtual=虛擬結算法（回收過滿站→赤字區集散場）、full=絕對上界(每晚還原站點到04:00目標)。")
    return parser.parse_args()


def _parse_alloc_arg(value: str) -> dict[str, int] | None:
    """custom 配置：優先當 utf-8 檔路徑讀（避免 Windows argv 把中文區名 cp950 損毀）；
    否則當 inline JSON 字串。空字串 → None。"""

    text = str(value).strip()
    if not text:
        return None
    p = Path(text)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(text)


def main() -> None:
    # Windows 終端機預設 cp950 無法編碼 ≤ 等字元；改用 utf-8 避免 console 輸出崩潰。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    args = parse_args()
    # 未指定 arrival-dir 時，依 profile 自動選 weekday / weekend clean 目錄。
    arrival_dir = args.arrival_dir or (
        PROJECT_ROOT / f"data/processed/arrival_rates_{args.profile}_clean"
    )
    patrol_starts = tuple(
        float(x) for x in str(args.patrol_starts).split(",") if x.strip()
    )
    preposition_minutes = tuple(
        float(x) for x in str(args.preposition_minutes).split(",") if x.strip()
    )
    duty_windows = None
    if str(args.duty_windows).strip():
        duty_windows = tuple(
            (float(seg.split("-")[0]), float(seg.split("-")[1]))
            for seg in str(args.duty_windows).split(",")
            if seg.strip()
        )
    # start_minute=-1 為自動：有快照 → 360(06:00)、否則 0（不影響既有實驗）。見 §32。
    start_minute = args.start_minute
    if start_minute < 0:
        start_minute = 360.0 if args.snapshot_csv is not None else 0.0
    config_override = {}
    if str(args.dispatch_config_override).strip():
        for pair in str(args.dispatch_config_override).split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                config_override[k.strip()] = float(v.strip())
    report_dir = run_real_system_scenario(
        hours=args.hours,
        seed=args.seed,
        profile=args.profile,
        label=args.label,
        output_root=args.output_root,
        station_capacity=args.station_capacity,
        initial_bikes=args.initial_bikes,
        speed_kmph=args.speed_kmph,
        min_trip_minutes=args.min_trip_minutes,
        arrival_dir=arrival_dir,
        transition_dir=args.transition_dir,
        visualization_input_dir=args.visualization_input_dir,
        capacity_json=args.capacity_json,
        circuity_mean=args.circuity_mean,
        circuity_sd=args.circuity_sd,
        speed_sd_kmph=args.speed_sd_kmph,
        rental_lognorm_mu=args.rental_lognorm_mu,
        rental_lognorm_sigma=args.rental_lognorm_sigma,
        rental_cap_minutes=args.rental_cap_minutes,
        self_station_ratio=args.self_station_ratio,
        self_lognorm_mu=args.self_lognorm_mu,
        self_lognorm_sigma=args.self_lognorm_sigma,
        excellent_benchmark_csv=args.excellent_benchmark_csv,
        dispatch_policy=args.dispatch_policy,
        trucks_per_district=args.trucks_per_district,
        truck_capacity=args.truck_capacity,
        truck_initial_load=args.truck_initial_load,
        truck_speed_kmph=args.truck_speed_kmph,
        truck_speed_sd_kmph=args.truck_speed_sd_kmph,
        depot_capacity=args.depot_capacity,
        depot_initial=args.depot_initial,
        cost_per_labor_hour=args.cost_per_labor_hour,
        cost_per_km=args.cost_per_km,
        cost_per_trip=args.cost_per_trip,
        cost_per_truck_fixed=args.cost_per_truck_fixed,
        min_action_ratio=args.min_action_ratio,
        demand_weight_alpha=args.demand_weight_alpha,
        forecast_base_ratio=args.forecast_base_ratio,
        forecast_horizon_hours=args.forecast_horizon_hours,
        preposition_minutes=preposition_minutes,
        patrol_starts=patrol_starts,
        patrol_duration=args.patrol_duration,
        report_subdir=args.report_subdir,
        duty_windows=duty_windows,
        total_fleet=args.total_fleet,
        maintenance_fraction=args.maintenance_fraction,
        depot_distribution=args.depot_distribution,
        depot_headroom=args.depot_headroom,
        start_minute=start_minute,
        snapshot_csv=args.snapshot_csv,
        config_override=config_override,
        variant_label=args.variant_label,
        truck_allocation_mode=args.truck_allocation,
        total_trucks=args.total_trucks,
        truck_allocation_custom=_parse_alloc_arg(args.truck_allocation_json),
        depot_init_override=_parse_alloc_arg(args.depot_init_json),
        days=args.days,
        overnight_mode=args.overnight_mode,
    )
    print(f"Report written to: {report_dir}")


if __name__ == "__main__":
    main()
