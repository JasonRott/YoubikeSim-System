"""把一次模擬的 event log 轉成「有趣的數據結果報告」。

提供：
- 每站時間加權指標：平均 fill ratio、低於 3 台車的時長、缺車（見車率）、滿站（見位率）。
- 全系統摘要與排名（最高/最低 fill、最常缺車、最忙站點）。
- 與真實 benchmark（kpis 的見車率 / 見位率）的對照。
- 文字敘述分析。
- 可直接印到 console 的格式化報告。
"""

from __future__ import annotations

from typing import Any, Optional


LOW_BIKE_THRESHOLD = 3

# 「優良時段占比」指標：06:00–23:59 內，站內車輛維持在容量 20%–80% 的時間比例。
EXCELLENT_LOW_RATIO = 0.2
EXCELLENT_HIGH_RATIO = 0.8
DAY_WINDOW_START_MIN = 6 * 60      # 06:00
DAY_WINDOW_END_MIN = 24 * 60       # 24:00
# 「吃力站」門檻：優良時段占比低於此值的站點視為被服務不足（≈真實 benchmark 的 P10≈0.33）。
EXCELLENT_POOR_THRESHOLD = 0.30


def _excellent_fraction(
    events: list[tuple[float, int]],
    initial_bikes: int,
    capacity: int,
    window_start: float,
    window_end: float,
) -> Optional[float]:
    """某站在 [window_start, window_end) 內，車輛落在 20%–80% 容量的時間比例。"""

    if capacity <= 0 or window_end <= window_start:
        return None
    low = EXCELLENT_LOW_RATIO * capacity
    high = EXCELLENT_HIGH_RATIO * capacity
    total = window_end - window_start
    excellent = 0.0

    current = initial_bikes
    segment_start = 0.0
    for time, bikes in sorted(events):
        lo = max(segment_start, window_start)
        hi = min(time, window_end)
        if hi > lo and low <= current <= high:
            excellent += hi - lo
        current = bikes
        segment_start = time
    lo = max(segment_start, window_start)
    if window_end > lo and low <= current <= high:
        excellent += window_end - lo
    return excellent / total


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[int(p / 100 * (len(ordered) - 1))]


def per_day_excellent(
    event_log: list[dict[str, Any]],
    capacity_by_station: dict[str, int],
    initial_by_station: dict[str, int],
    days: int,
) -> list[float]:
    """連續多日（§43）：逐日優良占比（每天評分窗 06:00–24:00 = [(k-1)*1440+360, k*1440)）。

    重用 `_excellent_fraction`（會從頭追蹤車量、只積分窗內），故傳整段事件 + day1 初始即可正確取各日窗。
    """
    events_by_station: dict[str, list[tuple[float, int]]] = {}
    for event in event_log:
        if event.get("event_type") not in ("rental", "return"):
            continue
        sid = event.get("station_id")
        ba = event.get("bikes_after")
        if sid is None or ba in (None, ""):
            continue
        events_by_station.setdefault(sid, []).append((float(event["time"]), int(ba)))
    out: list[float] = []
    for k in range(1, days + 1):
        ws, we = (k - 1) * 1440 + DAY_WINDOW_START_MIN, k * 1440
        fracs = []
        for sid, cap in capacity_by_station.items():
            if cap <= 0:
                continue
            f = _excellent_fraction(events_by_station.get(sid, []), int(initial_by_station.get(sid, 0)), cap, ws, we)
            if f is not None:
                fracs.append(f)
        out.append(round(sum(fracs) / len(fracs), 4) if fracs else 0.0)
    return out


def per_day_service_level(event_log: list[dict[str, Any]], days: int) -> list[Optional[float]]:
    """連續多日（§43）：逐日服務水準 SL = 1−(缺車+滿站)/(借+還互動)，同評分窗 06:00–24:00。

    供 SC 軌跡分析（看 none 的 SC 是否隨天數掉到 virtual 以下）。
    """
    buckets = [{"shortage": 0, "full_station": 0, "rental": 0, "return": 0} for _ in range(days)]
    for e in event_log:
        et = e.get("event_type")
        if et not in ("shortage", "full_station", "rental", "return"):
            continue
        t = float(e.get("time", 0.0))
        k = int(t // 1440)
        if k < 0 or k >= days:
            continue
        if (t - k * 1440) < DAY_WINDOW_START_MIN:   # 06:00 前（暖機）不計
            continue
        buckets[k][et] += 1
    out: list[Optional[float]] = []
    for c in buckets:
        denom = (c["shortage"] + c["rental"]) + (c["full_station"] + c["return"])
        out.append(round(1.0 - (c["shortage"] + c["full_station"]) / denom, 4) if denom else None)
    return out


def _bikes_at(events_sorted: list[tuple[float, int]], initial_bikes: int, time: float) -> int:
    """二分搜尋某站在 time 時刻的車輛數（階梯函式）。events_sorted 已依時間排序。"""

    left, right, index = 0, len(events_sorted) - 1, -1
    while left <= right:
        mid = (left + right) // 2
        if events_sorted[mid][0] <= time:
            index = mid
            left = mid + 1
        else:
            right = mid - 1
    return events_sorted[index][1] if index >= 0 else initial_bikes


def _station_time_metrics(
    events: list[tuple[float, int]],
    initial_bikes: int,
    capacity: int,
    horizon: float,
    window_start: float = 0.0,
) -> dict[str, float]:
    """以階梯函式對時間加權，算出一站的佔用指標。

    events：已排序的 (time, bikes_after)。期間內某時刻的車數是上一個事件後的值。
    window_start：積分起點（預設 0）。06:00 起跑（快照）時設 360，避免把未模擬的
    [0, window_start) 期間以 initial 灌入平均，見 architecture_narrative §32。
    """

    events = sorted((min(t, horizon), b) for t, b in events if t >= window_start)
    segments: list[tuple[float, int]] = []
    prev_time = window_start
    prev_bikes = initial_bikes
    for time, bikes in events:
        if time > prev_time:
            segments.append((time - prev_time, prev_bikes))
        prev_bikes = bikes
        prev_time = time
    if horizon > prev_time:
        segments.append((horizon - prev_time, prev_bikes))

    total = sum(duration for duration, _ in segments) or (horizon - window_start)
    avg_bikes = sum(duration * bikes for duration, bikes in segments) / total
    minutes_low = sum(d for d, b in segments if b < LOW_BIKE_THRESHOLD)
    minutes_empty = sum(d for d, b in segments if b <= 0)
    minutes_full = sum(d for d, b in segments if b >= capacity)
    return {
        "avg_bikes": avg_bikes,
        "avg_fill_ratio": avg_bikes / capacity if capacity else 0.0,
        "minutes_low": minutes_low,
        "frac_low": minutes_low / total,
        "frac_has_bike": 1.0 - minutes_empty / total,
        "frac_has_dock": 1.0 - minutes_full / total,
        "minutes_empty": minutes_empty,
        "minutes_full": minutes_full,
    }


def compute_run_metrics(
    event_log: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    capacity_by_station: dict[str, int],
    initial_by_station: dict[str, int],
    station_positions: dict[str, dict[str, Any]],
    benchmark_by_station: dict[str, dict[str, Any]],
    simulation_minutes: float,
    excellent_benchmark: Optional[dict[str, Any]] = None,
    window_start: float = 0.0,
) -> dict[str, Any]:
    """計算單次模擬的完整指標與排名。

    window_start：模擬起點分鐘（06:00 快照起跑時為 360）。佔用指標自此積分；
    優良時段窗仍固定 max(06:00, window_start)。見 architecture_narrative §32。
    """

    # 收集每站 rental/return 事件後的車數時間序列。
    events_by_station: dict[str, list[tuple[float, int]]] = {}
    rentals: dict[str, int] = {}
    returns: dict[str, int] = {}
    for event in event_log:
        event_type = event.get("event_type")
        if event_type not in ("rental", "return"):
            continue
        station_id = event.get("station_id")
        bikes_after = event.get("bikes_after")
        if station_id is None or bikes_after in (None, ""):
            continue
        events_by_station.setdefault(station_id, []).append(
            (float(event["time"]), int(bikes_after))
        )
        if event_type == "rental":
            rentals[station_id] = rentals.get(station_id, 0) + 1
        else:
            returns[station_id] = returns.get(station_id, 0) + 1

    def short_label(station_id: str) -> str:
        position = station_positions.get(station_id, {})
        return position.get("short_id", station_id)

    def district_of(station_id: str) -> str:
        return station_positions.get(station_id, {}).get("district", "")

    per_station: list[dict[str, Any]] = []
    for snapshot in snapshots:
        station_id = str(snapshot["station_id"])
        capacity = int(snapshot.get("capacity") or capacity_by_station.get(station_id, 0))
        initial = initial_by_station.get(station_id, int(snapshot.get("available_bikes", 0)))
        station_events = sorted(events_by_station.get(station_id, []))
        metrics = _station_time_metrics(
            station_events,
            initial,
            capacity,
            simulation_minutes,
            window_start,
        )
        excellent = _excellent_fraction(
            station_events,
            initial,
            capacity,
            max(DAY_WINDOW_START_MIN, window_start),
            min(DAY_WINDOW_END_MIN, simulation_minutes),
        )
        per_station.append(
            {
                "station_id": station_id,
                "short_id": short_label(station_id),
                "district": district_of(station_id),
                "capacity": capacity,
                "initial_bikes": initial,
                "final_bikes": int(snapshot.get("available_bikes", 0)),
                "rentals": rentals.get(station_id, 0),
                "returns": returns.get(station_id, 0),
                "excellent": excellent,
                "_events": station_events,
                **metrics,
            }
        )

    active = [s for s in per_station if s["capacity"] > 0]
    station_count = len(active)
    mean_fill = (
        sum(s["avg_fill_ratio"] for s in active) / station_count if station_count else 0.0
    )
    mean_has_bike = (
        sum(s["frac_has_bike"] for s in active) / station_count if station_count else 0.0
    )
    mean_has_dock = (
        sum(s["frac_has_dock"] for s in active) / station_count if station_count else 0.0
    )

    # 真實 benchmark 平均（只取有 benchmark 的站點）。
    bench_bike = [
        b["bike_avail_rate"]
        for b in benchmark_by_station.values()
        if b.get("bike_avail_rate") is not None
    ]
    bench_dock = [
        b["dock_avail_rate"]
        for b in benchmark_by_station.values()
        if b.get("dock_avail_rate") is not None
    ]
    real_bike_rate = sum(bench_bike) / len(bench_bike) if bench_bike else None
    real_dock_rate = sum(bench_dock) / len(bench_dock) if bench_dock else None

    def top(key, reverse=True, n=8):
        return sorted(active, key=lambda s: s[key], reverse=reverse)[:n]

    def slim(rows):
        return [
            {
                "short_id": r["short_id"],
                "station_id": r["station_id"],
                "district": r["district"],
                "capacity": r["capacity"],
                "avg_fill_ratio": round(r["avg_fill_ratio"], 3),
                "minutes_low": round(r["minutes_low"], 1),
                "rentals": r["rentals"],
                "returns": r["returns"],
            }
            for r in rows
        ]

    stations_under_3_any = sum(1 for s in active if s["minutes_low"] > 0)
    stations_ever_empty = sum(1 for s in active if s["minutes_empty"] > 0)
    stations_ever_full = sum(1 for s in active if s["minutes_full"] > 0)

    # 「優良時段占比」指標（需涵蓋日間 06:00 之後才有意義）。
    excellent_values = [s["excellent"] for s in active if s.get("excellent") is not None]
    excellent_block: Optional[dict[str, Any]] = None
    if excellent_values:
        worst_excellent = sorted(
            (s for s in active if s.get("excellent") is not None),
            key=lambda s: s["excellent"],
        )[:8]
        # 逐行政區優良占比彙整：每區站數、優良占比均值、優良占比 < 門檻 的站點比例（吃力站）。
        by_district: dict[str, list[float]] = {}
        for s in active:
            if s.get("excellent") is None:
                continue
            by_district.setdefault(s["district"], []).append(s["excellent"])
        # 逐行政區事件計數（供各區 ServiceLevel = 1 − (缺+滿)/(借互動+還互動)）。
        dev: dict[str, dict[str, int]] = {}
        for e in event_log:
            et = e.get("event_type")
            if et not in ("shortage", "full_station", "rental", "return"):
                continue
            did = e.get("district_id")
            if did is None:
                continue
            d = dev.setdefault(did, {"shortage": 0, "full_station": 0, "rental": 0, "return": 0})
            d[et] += 1

        def _district_sl(dist: str) -> Optional[float]:
            c = dev.get(dist)
            if not c:
                return None
            denom = (c["shortage"] + c["rental"]) + (c["full_station"] + c["return"])
            return round(1.0 - (c["shortage"] + c["full_station"]) / denom, 4) if denom else None

        district_breakdown = {
            dist: {
                "n_stations": len(vals),
                "excellent_mean": round(sum(vals) / len(vals), 4),
                "n_below_poor": sum(1 for v in vals if v < EXCELLENT_POOR_THRESHOLD),
                "frac_below_poor": round(
                    sum(1 for v in vals if v < EXCELLENT_POOR_THRESHOLD) / len(vals), 4
                ),
                "service_level": _district_sl(dist),
            }
            for dist, vals in sorted(by_district.items())
        }
        excellent_block = {
            "window": "06:00-24:00",
            "band": [EXCELLENT_LOW_RATIO, EXCELLENT_HIGH_RATIO],
            "station_count": len(excellent_values),
            "mean": round(sum(excellent_values) / len(excellent_values), 4),
            "median": round(_percentile(excellent_values, 50), 4),
            "p5": round(_percentile(excellent_values, 5), 4),
            "p10": round(_percentile(excellent_values, 10), 4),
            "p90": round(_percentile(excellent_values, 90), 4),
            "frac_all_day_healthy": round(
                sum(1 for v in excellent_values if v >= 0.999) / len(excellent_values), 4
            ),
            "frac_extreme_bad": round(
                sum(1 for v in excellent_values if v <= 0.25) / len(excellent_values), 4
            ),
            "poor_threshold": EXCELLENT_POOR_THRESHOLD,
            "frac_below_poor": round(
                sum(1 for v in excellent_values if v < EXCELLENT_POOR_THRESHOLD)
                / len(excellent_values),
                4,
            ),
            "district_breakdown": district_breakdown,
            "benchmark": excellent_benchmark,
            "worst_stations": [
                {
                    "short_id": s["short_id"],
                    "district": s["district"],
                    "capacity": s["capacity"],
                    "excellent": round(s["excellent"], 3),
                }
                for s in worst_excellent
            ],
        }

    # 每小時系統軌跡：各時刻「健康站比例」（車輛在 20–80% 容量）與平均填充率，
    # 用來量化 time-to-fail（從幾點開始跌破真實 benchmark 水準）。
    benchmark_mean = (
        excellent_benchmark.get("mean") if excellent_benchmark else None
    )
    trajectory: list[dict[str, float]] = []
    time_to_fail_hour: Optional[float] = None
    for hour in range(0, 25):
        t = hour * 60.0
        if t > simulation_minutes:
            break
        healthy = 0
        fill_sum = 0.0
        for s in active:
            bikes = _bikes_at(s["_events"], s["initial_bikes"], t)
            ratio = bikes / s["capacity"]
            fill_sum += ratio
            if EXCELLENT_LOW_RATIO <= ratio <= EXCELLENT_HIGH_RATIO:
                healthy += 1
        healthy_fraction = healthy / station_count if station_count else 0.0
        trajectory.append(
            {
                "hour": hour,
                "healthy_fraction": round(healthy_fraction, 4),
                "mean_fill": round(fill_sum / station_count, 4) if station_count else 0.0,
            }
        )
        # time-to-fail：日間（>=06:00）首次健康站比例跌破真實 benchmark 平均。
        if (
            time_to_fail_hour is None
            and hour >= 6
            and benchmark_mean is not None
            and healthy_fraction < benchmark_mean
        ):
            time_to_fail_hour = hour

    # 各行政區平均 fill。
    district_fill: dict[str, list[float]] = {}
    for s in active:
        district_fill.setdefault(s["district"], []).append(s["avg_fill_ratio"])
    district_avg = {
        d: round(sum(v) / len(v), 3) for d, v in district_fill.items()
    }

    return {
        "simulation_minutes": simulation_minutes,
        "station_count": station_count,
        "mean_fill_ratio": round(mean_fill, 4),
        "mean_frac_has_bike": round(mean_has_bike, 4),
        "mean_frac_has_dock": round(mean_has_dock, 4),
        "real_bike_avail_rate": round(real_bike_rate, 4) if real_bike_rate is not None else None,
        "real_dock_avail_rate": round(real_dock_rate, 4) if real_dock_rate is not None else None,
        "stations_under_3_any": stations_under_3_any,
        "stations_ever_empty": stations_ever_empty,
        "stations_ever_full": stations_ever_full,
        "district_avg_fill": dict(sorted(district_avg.items(), key=lambda kv: kv[1])),
        "excellent": excellent_block,
        "hourly_trajectory": trajectory,
        "time_to_fail_hour": time_to_fail_hour,
        "top_fill": slim(top("avg_fill_ratio", reverse=True)),
        "bottom_fill": slim(top("avg_fill_ratio", reverse=False)),
        "most_minutes_low": slim(top("minutes_low", reverse=True)),
        "busiest": slim(sorted(active, key=lambda s: s["rentals"] + s["returns"], reverse=True)[:8]),
    }


def _scrm(sc_block: dict[str, Any] | None) -> Optional[float]:
    """取 SC Ratio ×1e6；舊 summary 無此欄時，由 service_level/dispatching_cost 回算。"""

    sc = sc_block or {}
    val = sc.get("sc_ratio_per_million")
    if val is not None:
        return val
    sl = sc.get("service_level")
    cost = sc.get("dispatching_cost")
    if sl is not None and cost:
        return round(sl / cost * 1e6, 4)
    return None


POLICY_NAMES = {
    "none": "無調度 baseline",
    "fixed": "P1 固定巡迴",
    "dynamic": "P2 動態觸發",
    "hybrid_anticipatory": "P3 預置+反應",
    "hybrid_smartshift": "P4 智慧班次",
    "hybrid_forecast": "P6 預測式預置",
    "pair_coord": "P7 配對協調",
    "optimal_ub": "最佳化上界",
}


def build_narrative(
    metrics: dict[str, Any],
    profile: str,
    dispatch: dict[str, Any] | None = None,
    sc_block: dict[str, Any] | None = None,
) -> list[str]:
    """根據指標產生文字分析（中文敘述），含調度政策/成本與 true-standard 對照。"""

    minutes = metrics["simulation_minutes"]
    lines: list[str] = []

    mean_fill_pct = metrics["mean_fill_ratio"] * 100
    lines.append(
        f"這次 {profile} 模擬共 {minutes:.0f} 分鐘、{metrics['station_count']} 個站點，"
        f"全系統時間加權平均填充率約 {mean_fill_pct:.1f}%。"
    )

    sim_bike = metrics["mean_frac_has_bike"]
    real_bike = metrics["real_bike_avail_rate"]
    sim_dock = metrics["mean_frac_has_dock"]
    real_dock = metrics["real_dock_avail_rate"]
    if real_bike is not None:
        gap = (sim_bike - real_bike) * 100
        direction = "高於" if gap >= 0 else "低於"
        lines.append(
            f"模擬見車率（至少有 1 台車的時間比例）平均 {sim_bike * 100:.1f}%，"
            f"真實 benchmark 為 {real_bike * 100:.1f}%，模擬{direction}真實約 {abs(gap):.1f} 個百分點。"
        )
    if real_dock is not None:
        gap = (sim_dock - real_dock) * 100
        direction = "高於" if gap >= 0 else "低於"
        lines.append(
            f"模擬見位率（至少有 1 個空格的時間比例）平均 {sim_dock * 100:.1f}%，"
            f"真實 benchmark 為 {real_dock * 100:.1f}%，模擬{direction}真實約 {abs(gap):.1f} 個百分點。"
        )

    excellent = metrics.get("excellent")
    if excellent:
        sentence = (
            f"優良時段占比（06:00–24:00 內車輛維持 20–80% 容量的時間比例）系統平均 "
            f"{excellent['mean'] * 100:.1f}%、中位 {excellent['median'] * 100:.1f}%、"
            f"P10 {excellent['p10'] * 100:.0f}%、極端惡劣(≤25%)站占 {excellent['frac_extreme_bad'] * 100:.1f}%。"
        )
        bench = excellent.get("benchmark")
        if bench and bench.get("mean") is not None:
            gap = (excellent["mean"] - bench["mean"]) * 100
            direction = "高於" if gap >= 0 else "低於"
            if gap >= 0:
                verdict = "（已達/超越真實 true standard）"
            elif abs(gap) <= 5:
                verdict = "（接近真實 true standard，仍略低）"
            else:
                verdict = "（明顯低於真實 true standard）"
            sentence += (
                f" 真實 true standard 平均 {bench['mean'] * 100:.1f}%、中位 {bench['median'] * 100:.1f}%；"
                f"模擬{direction}真實約 {abs(gap):.1f} 個百分點" + verdict + "。"
            )
        lines.append(sentence)

    traj = metrics.get("hourly_trajectory") or []
    if traj:
        def healthy_at(hour):
            for p in traj:
                if p["hour"] == hour:
                    return p["healthy_fraction"]
            return None
        ttf = metrics.get("time_to_fail_hour")
        h6, h12, h18, h22 = healthy_at(6), healthy_at(12), healthy_at(18), healthy_at(22)
        parts = []
        if h6 is not None:
            parts.append(f"06:00 健康站 {h6 * 100:.0f}%")
        if h12 is not None:
            parts.append(f"12:00 {h12 * 100:.0f}%")
        if h18 is not None:
            parts.append(f"18:00 {h18 * 100:.0f}%")
        if h22 is not None:
            parts.append(f"22:00 {h22 * 100:.0f}%")
        sentence = "系統健康站比例隨時間下滑（" + "、".join(parts) + "）。"
        if ttf is not None:
            sentence += f" 約在 {int(ttf):02d}:00 跌破真實 benchmark 水準（time-to-fail）。"
        else:
            sentence += " 全日皆未跌破真實 benchmark 水準。"
        lines.append(sentence)

    lines.append(
        f"有 {metrics['stations_under_3_any']} 個站點曾經少於 {LOW_BIKE_THRESHOLD} 台車，"
        f"其中 {metrics['stations_ever_empty']} 站曾完全無車、"
        f"{metrics['stations_ever_full']} 站曾滿站無空位。"
    )

    if metrics["bottom_fill"]:
        worst = metrics["bottom_fill"][0]
        lines.append(
            f"填充率最低的站點是 {worst['short_id']}（{worst['district']}），"
            f"平均僅 {worst['avg_fill_ratio'] * 100:.0f}%，容量 {worst['capacity']} 格。"
        )
    if metrics["most_minutes_low"]:
        stressed = metrics["most_minutes_low"][0]
        if stressed["minutes_low"] > 0:
            lines.append(
                f"缺車壓力最大的是 {stressed['short_id']}（{stressed['district']}），"
                f"在模擬期間有 {stressed['minutes_low']:.0f} 分鐘少於 {LOW_BIKE_THRESHOLD} 台車。"
            )

    district_avg = metrics["district_avg_fill"]
    if district_avg:
        low_districts = list(district_avg.items())[:3]
        names = "、".join(f"{d}({v * 100:.0f}%)" for d, v in low_districts)
        lines.append(f"平均填充率較低的行政區：{names}。")

    # 新增觀察指標：吃力站最多的行政區 + 集散場平均車量最低的行政區。
    breakdown = (metrics.get("excellent") or {}).get("district_breakdown")
    if breakdown:
        thr = int(round((metrics["excellent"].get("poor_threshold", 0.3)) * 100))
        worst = sorted(breakdown.items(), key=lambda kv: -kv[1].get("frac_below_poor", 0))[:3]
        names = "、".join(
            f"{d}({b['frac_below_poor'] * 100:.0f}%、{b['n_below_poor']}/{b['n_stations']})"
            for d, b in worst
        )
        lines.append(f"優良占比<{thr}% 吃力站比例最高的行政區：{names}。")
        depot_avg = (dispatch or {}).get("depot_avg_bikes")
        if depot_avg:
            low_depots = sorted(depot_avg.items(), key=lambda kv: kv[1])[:3]
            dnames = "、".join(f"{d}({v:.0f}台)" for d, v in low_depots)
            lines.append(
                f"集散場平均車量最低的行政區：{dnames}（集散場為成本/觀察項，非品質標準）。"
            )

    policy = (dispatch or {}).get("policy", "none")
    if minutes < 1440:
        lines.append(
            "提醒：此為短時長測試，優良時段占比需 24 小時全日模擬才有意義；初始車輛以真實 avg_fill_ratio 設定。"
        )
        return lines

    if policy in (None, "none"):
        lines.append(
            "本次為無調度（no-dispatch）模擬，是服務 floor；與真實 true standard 的差距即為調度能著力的最大空間。"
        )
        return lines

    # 有調度政策：補上政策/成本摘要 + 是否達真實 true standard 的判定。
    st = (dispatch or {}).get("stats", {})
    cb = (dispatch or {}).get("cost_breakdown", {})
    sc = sc_block or {}
    name = POLICY_NAMES.get(policy, policy)
    lines.append(
        f"本次為 {name} 調度（{(dispatch or {}).get('truck_count', 0)} 車、每區 "
        f"{(dispatch or {}).get('trucks_per_district', 0)} 台）：出車 {st.get('trips', 0)} 次、"
        f"里程 {st.get('total_km', 0):.0f} km、計薪工時 {st.get('on_duty_hours', 0):.0f} hr，"
        f"調度成本約 NT${sc.get('dispatching_cost', 0):,.0f}"
        f"（人力 {cb.get('labor', 0):,.0f} 為大宗），"
        f"ServiceLevel {sc.get('service_level')}、SC Ratio {_scrm(sc)}×10⁻⁶。"
    )
    excellent = metrics.get("excellent") or {}
    bench = (excellent.get("benchmark") or {}).get("mean")
    em = excellent.get("mean")
    if bench is not None and em is not None:
        if em >= bench:
            lines.append(
                f"優良時段占比 {em * 100:.1f}% 已達/超越真實 true standard {bench * 100:.1f}%"
                "——此政策不輸現實營運。"
            )
        else:
            lines.append(
                f"優良時段占比 {em * 100:.1f}% 仍低於真實 true standard {bench * 100:.1f}%"
                f"（差 {(bench - em) * 100:.1f} 個百分點）——尚未真正勝過現實，"
                "即仍在『以較低品質換取較低成本』，須補上跨區調度等能力才可能達標。"
            )
    return lines


def _format_table(title: str, rows: list[dict[str, Any]], value_key: str, value_label: str) -> list[str]:
    lines = [f"### {title}", "", f"| 短ID | 行政區 | 容量 | {value_label} | 借/還 |", "|---|---|---|---|---|"]
    for r in rows:
        value = r[value_key]
        value_str = f"{value * 100:.0f}%" if value_key == "avg_fill_ratio" else f"{value:.0f}"
        lines.append(
            f"| {r['short_id']} | {r['district']} | {r['capacity']} | {value_str} | {r['rentals']}/{r['returns']} |"
        )
    lines.append("")
    return lines


def format_district_lines(
    excellent: dict[str, Any] | None, dispatch: dict[str, Any] | None
) -> list[str]:
    """逐行政區觀察：集散場平均車量（成本/觀察項）+ 站點優良占比均值 + 優良<門檻 的吃力站比例。"""

    breakdown = (excellent or {}).get("district_breakdown")
    if not breakdown:
        return []
    depot_avg = (dispatch or {}).get("depot_avg_bikes", {})
    dcost = (dispatch or {}).get("district_cost", {})
    total_cost = sum(c.get("total", 0) for c in dcost.values()) if dcost else 0.0
    thr = int(round((excellent or {}).get("poor_threshold", EXCELLENT_POOR_THRESHOLD) * 100))
    lines = [
        "",
        f"## 行政區觀察（集散場平均車量 + 優良占比 + 吃力站<{thr}% + 成本佔比 + 各區SC）",
        "",
        "> 集散場狀況視為**成本/觀察項**，不作為品質標準（不要求一定要有車）。",
        "",
        f"| 行政區 | 集散場平均車量 | 優良占比均值 | 優良<{thr}% 吃力站 | 成本佔比 | 各區SC×10⁻⁶ |",
        "|---|---|---|---|---|---|",
    ]
    # 以「吃力站比例」由高到低排序，先看問題行政區。
    for dist, b in sorted(
        breakdown.items(), key=lambda kv: -kv[1].get("frac_below_poor", 0)
    ):
        davg = depot_avg.get(dist)
        davg_str = f"{davg:.0f}" if davg is not None else "—"
        c = dcost.get(dist, {})
        ctot = c.get("total", 0)
        share = f"{ctot / total_cost * 100:.1f}%" if total_cost > 0 else "—"
        sl = b.get("service_level")
        scd = f"{sl / ctot * 1e6:.2f}" if (sl is not None and ctot) else "—"
        lines.append(
            f"| {dist} | {davg_str} | {b['excellent_mean'] * 100:.1f}% | "
            f"{b['n_below_poor']}/{b['n_stations']}（{b['frac_below_poor'] * 100:.1f}%） | "
            f"{share} | {scd} |"
        )
    lines.append("")
    return lines


def format_dispatch_lines(
    dispatch: dict[str, Any] | None,
    sc_block: dict[str, Any] | None,
    excellent: dict[str, Any] | None,
) -> list[str]:
    """產生 report.md 的「調度與成本」+「vs 真實 true standard」區塊。"""

    if not dispatch:
        return []
    policy = dispatch.get("policy", "none")
    name = POLICY_NAMES.get(policy, policy)
    lines = ["", "## 調度與成本", "", f"- 政策：**{name}**"]
    if policy in (None, "none"):
        lines.append("- 無調度 baseline（服務 floor），無調度成本。")
    else:
        st = dispatch.get("stats", {})
        cb = dispatch.get("cost_breakdown", {})
        sc = sc_block or {}
        lines += [
            f"- 車隊：{dispatch.get('truck_count', 0)} 車（每區 {dispatch.get('trucks_per_district', 0)} 台）",
            f"- 出車次數：{st.get('trips', 0)}　總里程：{st.get('total_km', 0):.0f} km　"
            f"搬運：{st.get('bikes_moved', 0)} 台（補 {st.get('bikes_replenished', 0)}／抽 {st.get('bikes_withdrawn', 0)}）",
            f"- 計薪工時：{st.get('on_duty_hours', 0):.0f} hr　休息：{st.get('rest_minutes', 0):.0f} min　白跑：{st.get('wasted_visits', 0)} 次",
            "",
            "| 成本分項 (NT$) | 人力 | 里程 | 出車 | 固定 | 合計 |",
            "|---|---|---|---|---|---|",
            f"| 金額 | {cb.get('labor', 0):,.0f} | {cb.get('mileage', 0):,.0f} | "
            f"{cb.get('trip', 0):,.0f} | {cb.get('fixed', 0):,.0f} | **{cb.get('total', 0):,.0f}** |",
            "",
            f"- **ServiceLevel**（1−(缺車+滿站)/(借+還互動)）：{sc.get('service_level')}",
            f"- **SC Ratio**（ServiceLevel/成本）：{_scrm(sc)}×10⁻⁶",
        ]
    # vs 真實 true standard
    bench = ((excellent or {}).get("benchmark") or {}).get("mean")
    em = (excellent or {}).get("mean")
    if bench is not None and em is not None:
        meet = em >= bench
        lines += [
            "",
            "## vs 真實 true standard（優良時段占比 06:00–24:00）",
            "",
            f"- 本政策優良占比：**{em * 100:.1f}%**　真實 true standard：**{bench * 100:.1f}%**",
            (
                f"- ✅ 已達/超越 true standard（不輸現實營運）。"
                if meet
                else f"- ❌ 低於 true standard 約 {(bench - em) * 100:.1f} 個百分點"
                "——尚未真正勝過現實（仍在『以較低品質換較低成本』）。"
            ),
        ]
    return lines


def format_report_lines(
    metrics: dict[str, Any],
    narrative: list[str],
    dispatch: dict[str, Any] | None = None,
    sc_block: dict[str, Any] | None = None,
) -> list[str]:
    """產生 report.md 用的指標區塊。"""

    lines = ["", "## 數據結果分析", ""]
    lines += [f"- {sentence}" for sentence in narrative]
    lines += format_dispatch_lines(dispatch, sc_block, metrics.get("excellent"))
    lines += format_district_lines(metrics.get("excellent"), dispatch)
    lines += [
        "",
        "## 系統指標",
        "",
        f"- 平均填充率：{metrics['mean_fill_ratio'] * 100:.1f}%",
        f"- 模擬見車率：{metrics['mean_frac_has_bike'] * 100:.1f}%"
        + (
            f"（真實 {metrics['real_bike_avail_rate'] * 100:.1f}%）"
            if metrics["real_bike_avail_rate"] is not None
            else ""
        ),
        f"- 模擬見位率：{metrics['mean_frac_has_dock'] * 100:.1f}%"
        + (
            f"（真實 {metrics['real_dock_avail_rate'] * 100:.1f}%）"
            if metrics["real_dock_avail_rate"] is not None
            else ""
        ),
        f"- 曾少於 {LOW_BIKE_THRESHOLD} 台車的站點數：{metrics['stations_under_3_any']}",
        f"- 曾無車的站點數：{metrics['stations_ever_empty']}",
        f"- 曾滿站的站點數：{metrics['stations_ever_full']}",
        "",
    ]
    excellent = metrics.get("excellent")
    if excellent:
        bench = excellent.get("benchmark") or {}
        lines += [
            "## 優良時段占比（06:00–24:00, 車輛維持 20–80% 容量）",
            "",
            "| 指標 | 模擬 | 真實 true standard |",
            "|---|---|---|",
            f"| 系統平均 | {excellent['mean'] * 100:.1f}% | "
            + (f"{bench['mean'] * 100:.1f}%" if bench.get('mean') is not None else "—") + " |",
            f"| 中位數 | {excellent['median'] * 100:.1f}% | "
            + (f"{bench['median'] * 100:.1f}%" if bench.get('median') is not None else "—") + " |",
            f"| P5 | {excellent['p5'] * 100:.1f}% | "
            + (f"{bench['p5'] * 100:.1f}%" if bench.get('p5') is not None else "—") + " |",
            f"| P10 | {excellent['p10'] * 100:.1f}% | "
            + (f"{bench['p10'] * 100:.1f}%" if bench.get('p10') is not None else "—") + " |",
            f"| P90 | {excellent['p90'] * 100:.1f}% | "
            + (f"{bench['p90'] * 100:.1f}%" if bench.get('p90') is not None else "—") + " |",
            f"| 全天健康(=100%)占比 | {excellent['frac_all_day_healthy'] * 100:.1f}% | "
            + (f"{bench['frac_all_day_healthy'] * 100:.1f}%" if bench.get('frac_all_day_healthy') is not None else "—") + " |",
            f"| 極端惡劣(≤25%)占比 | {excellent['frac_extreme_bad'] * 100:.1f}% | "
            + (f"{bench['frac_extreme_bad'] * 100:.1f}%" if bench.get('frac_extreme_bad') is not None else "—") + " |",
            "",
        ]
        lines += _format_table(
            "優良時段占比最低站點",
            [
                {
                    "short_id": w["short_id"],
                    "district": w["district"],
                    "capacity": w["capacity"],
                    "avg_fill_ratio": w["excellent"],
                    "rentals": "",
                    "returns": "",
                }
                for w in excellent["worst_stations"]
            ],
            "avg_fill_ratio",
            "優良占比",
        )
    lines += _format_table("填充率最高站點", metrics["top_fill"], "avg_fill_ratio", "平均填充率")
    lines += _format_table("填充率最低站點", metrics["bottom_fill"], "avg_fill_ratio", "平均填充率")
    lines += _format_table(
        f"缺車時長最長站點（少於 {LOW_BIKE_THRESHOLD} 台的分鐘數）",
        metrics["most_minutes_low"],
        "minutes_low",
        "缺車分鐘",
    )
    lines += _format_table("最忙站點（借＋還）", metrics["busiest"], "minutes_low", "缺車分鐘")
    return lines


def format_console(metrics: dict[str, Any], narrative: list[str]) -> str:
    """產生一段精簡、直接印到終端機的報告。"""

    sep = "=" * 60
    out = [sep, "  模擬數據結果報告", sep]
    out += [f"  {sentence}" for sentence in narrative]
    out.append("-" * 60)
    out.append(
        f"  平均填充率 {metrics['mean_fill_ratio'] * 100:.1f}% | "
        f"見車率 {metrics['mean_frac_has_bike'] * 100:.1f}%"
        + (
            f"(真實 {metrics['real_bike_avail_rate'] * 100:.1f}%)"
            if metrics["real_bike_avail_rate"] is not None
            else ""
        )
        + f" | 見位率 {metrics['mean_frac_has_dock'] * 100:.1f}%"
        + (
            f"(真實 {metrics['real_dock_avail_rate'] * 100:.1f}%)"
            if metrics["real_dock_avail_rate"] is not None
            else ""
        )
    )
    excellent = metrics.get("excellent")
    if excellent:
        bench = excellent.get("benchmark") or {}
        bench_str = (
            f"(真實 {bench['mean'] * 100:.1f}%)" if bench.get("mean") is not None else ""
        )
        out.append(
            f"  優良時段占比 系統平均 {excellent['mean'] * 100:.1f}%{bench_str} | "
            f"中位 {excellent['median'] * 100:.1f}% | P10 {excellent['p10'] * 100:.0f}% | "
            f"極端惡劣站 {excellent['frac_extreme_bad'] * 100:.1f}%"
        )
    out.append("  填充率最低 3 站：" + "、".join(
        f"{r['short_id']}({r['district']},{r['avg_fill_ratio'] * 100:.0f}%)"
        for r in metrics["bottom_fill"][:3]
    ))
    out.append(f"  缺車時長最長 3 站（<{LOW_BIKE_THRESHOLD}台分鐘）：" + "、".join(
        f"{r['short_id']}({r['minutes_low']:.0f}分)" for r in metrics["most_minutes_low"][:3]
    ))
    out.append(sep)
    return "\n".join(out)
