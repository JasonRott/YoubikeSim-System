"""將 simple scenario 的報告資料轉成單一 HTML 視覺化檔案。

輸出的 visualization.html 有兩個頁籤：
1. 摘要報告：檢查 OD 比例、旅行時間與站點狀態。
2. 動態播放：用事件紀錄重播 rider 在系統內騎乘的狀態。
"""

from __future__ import annotations

import argparse
import csv
from html import escape
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def latest_report(report_root: Path) -> Path:
    """取得最新的 simple scenario 報告資料夾。"""

    candidates = [
        path
        for path in report_root.glob("simple_scenario_*")
        if path.is_dir() and path.joinpath("summary.json").exists()
    ]
    if not candidates:
        raise FileNotFoundError(f"No simple_scenario reports found in {report_root}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def bar(width: float, color: str) -> str:
    """產生簡單橫條圖。"""

    bounded_width = max(0.0, min(100.0, width))
    return (
        f'<div class="bar-track"><div class="bar" '
        f'style="width:{bounded_width:.2f}%; background:{color};"></div></div>'
    )


def load_animation_routes(report_dir: Path) -> list[dict[str, Any]]:
    """從 events.csv 擷取可重播的 rider 路線資料。

    route_planned 事件代表 rider 已成功借車並開始旅程；因此動畫只需要這些事件。
    """

    events_path = report_dir / "events.csv"
    routes: list[dict[str, Any]] = []
    with events_path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("event_type") != "route_planned":
                continue

            start_time = float(row["time"])
            total_travel_time = float(row["total_travel_time"])
            routes.append(
                {
                    "riderId": row["rider_id"],
                    "origin": row["origin_station_id"],
                    "destination": row["destination_station_id"],
                    "originDistrict": row["origin_district_id"],
                    "destinationDistrict": row["destination_district_id"],
                    "start": start_time,
                    "end": start_time + total_travel_time,
                    "duration": total_travel_time,
                    "sameDistrict": row["origin_district_id"]
                    == row["destination_district_id"],
                }
            )
    return routes


def load_station_state_history(
    report_dir: Path,
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """從 rental/return 事件重建每個時間點的站點可借車數。

    events.csv 只記錄事件後的 bikes_after；為了得到 time=0 的初始狀態，
    這裡從模擬結束狀態反向套用 rental/return 事件的變化量。
    """

    station_states: dict[str, dict[str, Any]] = {}
    for snapshot in summary["station_snapshots"]:
        station_id = str(snapshot["station_id"])
        station_states[station_id] = {
            "district": str(snapshot["district_id"]),
            "capacity": int(snapshot["capacity"]),
            "finalBikes": int(snapshot["available_bikes"]),
            "events": [],
        }

    events_path = report_dir / "events.csv"
    station_events: dict[str, list[dict[str, Any]]] = {
        station_id: [] for station_id in station_states
    }
    with events_path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            event_type = row.get("event_type")
            station_id = row.get("station_id")
            bikes_after = row.get("bikes_after")
            if event_type not in {"rental", "return"}:
                continue
            if not station_id or not bikes_after:
                continue
            station_id = str(station_id)
            if station_id not in station_events:
                continue
            station_events[station_id].append(
                {
                    "time": float(row["time"]),
                    "eventType": event_type,
                    "bikes": int(float(bikes_after)),
                }
            )

    for station_id, events in station_events.items():
        events.sort(key=lambda event: event["time"])
        initial_bikes = station_states[station_id]["finalBikes"]
        for event in reversed(events):
            if event["eventType"] == "rental":
                initial_bikes += 1
            elif event["eventType"] == "return":
                initial_bikes -= 1

        station_states[station_id]["initialBikes"] = initial_bikes
        station_states[station_id]["events"] = [
            {"time": event["time"], "bikes": event["bikes"]} for event in events
        ]

    return station_states


def load_waiting_intervals(report_dir: Path) -> list[dict[str, Any]]:
    """從事件紀錄重建滿站等待 queue 的進出區間。

    rider 在 `return_wait_started` 時進入該站 queue；之後若成功還車、等待後完成，
    或開始 `search_nearby_station`，就視為離開 queue。
    """

    events_path = report_dir / "events.csv"
    rows: list[dict[str, str]] = []
    with events_path.open("r", newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    waiting_by_rider: dict[str, dict[str, Any]] = {}
    intervals: list[dict[str, Any]] = []
    for row in rows:
        event_type = row.get("event_type", "")
        rider_id = row.get("rider_id", "")
        if not rider_id:
            continue

        if event_type == "return_wait_started":
            start_time = float(row["time"])
            patience_time = float(row.get("patience_time") or 0)
            waiting_by_rider[rider_id] = {
                "riderId": rider_id,
                "station": row["station_id"],
                "start": start_time,
                "fallbackEnd": start_time + patience_time,
            }
            continue

        if rider_id not in waiting_by_rider:
            continue

        if event_type in {
            "return",
            "rider_finished_after_wait",
            "search_nearby_station",
        }:
            interval = waiting_by_rider.pop(rider_id)
            interval["end"] = float(row["time"])
            interval["exitEvent"] = event_type
            intervals.append(interval)

    for interval in waiting_by_rider.values():
        interval["end"] = interval["fallbackEnd"]
        interval["exitEvent"] = "patience_timeout_unobserved"
        intervals.append(interval)

    return [
        {
            "riderId": interval["riderId"],
            "station": interval["station"],
            "start": interval["start"],
            "end": interval["end"],
            "exitEvent": interval["exitEvent"],
        }
        for interval in intervals
        if interval["end"] >= interval["start"]
    ]


def render_html(
    summary: dict[str, Any],
    routes: list[dict[str, Any]],
    station_states: dict[str, dict[str, Any]],
    waiting_intervals: list[dict[str, Any]],
    report_dir: Path,
) -> str:
    """把 summary.json 與 events.csv 轉成可檢查的 HTML。"""

    same_ratio = summary["observed_same_district_ratio"]
    cross_ratio = summary["observed_cross_district_ratio"]
    max_route_count = max(summary["route_time_counts"].values() or [1])
    max_station_count = max(
        max(station["rental_count"], station["return_count"])
        for station in summary["station_snapshots"]
    )
    animation_routes_json = json.dumps(routes, ensure_ascii=False)
    station_states_json = json.dumps(station_states, ensure_ascii=False)
    waiting_intervals_json = json.dumps(waiting_intervals, ensure_ascii=False)
    simulation_minutes = float(summary["simulation_minutes"])

    route_rows = []
    for route_time, count in sorted(
        summary["route_time_counts"].items(),
        key=lambda item: float(item[0]),
    ):
        route_rows.append(
            "<tr>"
            f"<td>{escape(route_time)} min</td>"
            f"<td>{count}</td>"
            f"<td>{bar(count / max_route_count * 100, '#2f6f9f')}</td>"
            "</tr>"
        )

    station_rows = []
    for station in summary["station_snapshots"]:
        station_rows.append(
            "<tr>"
            f"<td>{escape(station['station_id'])}</td>"
            f"<td>{escape(station['district_id'])}</td>"
            f"<td>{station['available_bikes']}</td>"
            f"<td>{station['available_docks']}</td>"
            f"<td>{station['rental_count']} {bar(station['rental_count'] / max_station_count * 100, '#4d8f5f')}</td>"
            f"<td>{station['return_count']} {bar(station['return_count'] / max_station_count * 100, '#b66a3c')}</td>"
            f"<td>{station['shortage_count']}</td>"
            f"<td>{station['full_count']}</td>"
            "</tr>"
        )

    od_items = []
    max_od_count = max(summary["od_counts"].values() or [1])
    for od, count in summary["od_counts"].items():
        od_items.append(
            f"<li><span>{escape(od)}</span><strong>{count}</strong>"
            f"{bar(count / max_od_count * 100, '#6c6c6c')}</li>"
        )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>Simple Scenario Visualization</title>
  <style>
    body {{
      margin: 0;
      font-family: Arial, "Microsoft JhengHei", sans-serif;
      color: #1d1d1f;
      background: #f5f7f8;
      overflow-y: scroll;
      scrollbar-gutter: stable;
    }}
    header {{
      padding: 24px 32px;
      background: #233043;
      color: white;
    }}
    main {{
      padding: 24px 32px 40px;
      max-width: 1180px;
      min-height: calc(100vh - 132px);
      margin: 0 auto;
    }}
    section {{
      margin-bottom: 28px;
    }}
    h1, h2 {{
      margin: 0 0 12px;
      letter-spacing: 0;
    }}
    button, select, input {{
      font: inherit;
    }}
    .tabs {{
      display: flex;
      gap: 8px;
      margin-top: 18px;
    }}
    .tab-button {{
      border: 1px solid rgba(255, 255, 255, 0.35);
      background: rgba(255, 255, 255, 0.12);
      color: white;
      border-radius: 8px;
      padding: 9px 14px;
      cursor: pointer;
    }}
    .tab-button.active {{
      background: white;
      color: #233043;
    }}
    .page {{
      display: none;
    }}
    .page.active {{
      display: block;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .metric {{
      background: white;
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      padding: 14px;
    }}
    .metric span {{
      display: block;
      font-size: 13px;
      color: #59636f;
      margin-bottom: 6px;
    }}
    .metric strong {{
      font-size: 24px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px;
      border-bottom: 1px solid #ebeff2;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #edf2f5;
      font-size: 13px;
    }}
    .bar-track {{
      height: 8px;
      background: #e8ecef;
      border-radius: 999px;
      overflow: hidden;
      min-width: 120px;
      margin-top: 6px;
    }}
    .bar {{
      height: 100%;
    }}
    .network {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
    }}
    .district {{
      background: white;
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      padding: 16px;
    }}
    .stations {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
    }}
    .station {{
      border: 1px solid #c9d3dc;
      border-radius: 8px;
      padding: 12px;
      text-align: center;
      background: #fbfcfd;
    }}
    .od-list {{
      columns: 2;
      background: white;
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      padding: 14px 20px;
    }}
    .od-list li {{
      break-inside: avoid;
      margin-bottom: 10px;
      list-style: none;
    }}
    .od-list span {{
      display: inline-block;
      min-width: 76px;
    }}
    .animation-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 18px;
      align-items: start;
    }}
    .animation-panel, .control-panel {{
      background: white;
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      padding: 16px;
    }}
    .controls {{
      display: grid;
      gap: 12px;
    }}
    .control-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}
    .control-row button {{
      border: 1px solid #b8c4ce;
      background: #ffffff;
      border-radius: 8px;
      padding: 8px 12px;
      cursor: pointer;
    }}
    .control-row button.primary {{
      background: #233043;
      color: white;
      border-color: #233043;
    }}
    .time-slider {{
      width: 100%;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 13px;
      color: #59636f;
      margin-top: 10px;
    }}
    .legend-dot {{
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-right: 5px;
    }}
    .svg-wrap {{
      width: 100%;
      overflow: hidden;
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      background: #fbfcfd;
    }}
    .graph-toolbar {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 10px;
    }}
    .graph-toolbar h2 {{
      margin: 0;
    }}
    .back-button {{
      border: 1px solid #b8c4ce;
      background: #ffffff;
      border-radius: 8px;
      padding: 8px 12px;
      cursor: pointer;
    }}
    .back-button[hidden] {{
      display: none;
    }}
    svg {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .route-line {{
      stroke: #c9d3dc;
      stroke-width: 2;
    }}
    .route-line.external-line {{
      stroke-dasharray: 8 7;
    }}
    .node-group {{
      cursor: pointer;
    }}
    .node-circle {{
      fill: white;
      stroke: #233043;
      stroke-width: 2;
    }}
    .node-circle.district-node {{
      fill: #edf4f7;
    }}
    .node-circle.station-node {{
      fill: #ffffff;
    }}
    .station-node {{
      fill: white;
      stroke: #233043;
      stroke-width: 2;
    }}
    .station-label {{
      font-size: 16px;
      font-weight: 700;
      fill: #233043;
      text-anchor: middle;
      dominant-baseline: middle;
    }}
    .node-count {{
      font-size: 12px;
      font-weight: 800;
      fill: #111827;
      stroke: #ffffff;
      stroke-width: 2.5px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
    }}
    .node-ratio {{
      font-size: 10px;
      fill: #1d1d1f;
      text-anchor: middle;
      dominant-baseline: middle;
    }}
    .external-count {{
      font-size: 11px;
      font-weight: 700;
      fill: #111827;
      stroke: #ffffff;
      stroke-width: 2px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
    }}
    .external-node {{
      fill: #f7f7f7;
      stroke: #7d8790;
      stroke-width: 2;
      stroke-dasharray: 5 5;
    }}
    .rider {{
      stroke: white;
      stroke-width: 2;
    }}
    .recent-routes {{
      list-style: none;
      padding: 0;
      margin: 8px 0 0;
      height: 260px;
      overflow: auto;
      font-size: 13px;
    }}
    .recent-routes li {{
      padding: 7px 0;
      border-bottom: 1px solid #edf1f4;
    }}
    .note {{
      color: #59636f;
      font-size: 13px;
      line-height: 1.5;
    }}
    @media (max-width: 860px) {{
      .animation-shell {{
        grid-template-columns: 1fr;
      }}
    }}
    @media (max-width: 760px) {{
      main, header {{
        padding-left: 16px;
        padding-right: 16px;
      }}
      .network {{
        grid-template-columns: 1fr;
      }}
      .od-list {{
        columns: 1;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Simple Scenario Visualization</h1>
    <div>{escape(str(report_dir))}</div>
    <nav class="tabs" aria-label="visualization pages">
      <button class="tab-button active" type="button" data-page="summary-page">摘要報告</button>
      <button class="tab-button" type="button" data-page="animation-page">動態播放</button>
    </nav>
  </header>
  <main>
    <div id="summary-page" class="page active">
      <section class="grid">
        <div class="metric"><span>Simulation Hours</span><strong>{summary['simulation_hours']:.1f}</strong></div>
        <div class="metric"><span>Expected Arrivals</span><strong>{summary['expected_total_arrivals']:.1f}</strong></div>
        <div class="metric"><span>Observed Arrivals</span><strong>{summary['observed_total_arrivals']}</strong></div>
        <div class="metric"><span>Successful Routes</span><strong>{summary['successful_routes']}</strong></div>
        <div class="metric"><span>Same District Ratio</span><strong>{same_ratio:.3f}</strong>{bar(same_ratio * 100, '#4d8f5f')}</div>
        <div class="metric"><span>Cross District Ratio</span><strong>{cross_ratio:.3f}</strong>{bar(cross_ratio * 100, '#b66a3c')}</div>
      </section>

      <section>
        <h2>Network</h2>
        <div class="network">
          <div class="district"><h2>District A</h2><div class="stations"><div class="station">A1</div><div class="station">A2</div><div class="station">A3</div></div></div>
          <div class="district"><h2>District B</h2><div class="stations"><div class="station">B1</div><div class="station">B2</div><div class="station">B3</div></div></div>
        </div>
      </section>

      <section>
        <h2>Travel Time Check</h2>
        <table>
          <thead><tr><th>Total Time</th><th>Count</th><th>Relative Frequency</th></tr></thead>
          <tbody>{''.join(route_rows)}</tbody>
        </table>
      </section>

      <section>
        <h2>Station State</h2>
        <table>
          <thead><tr><th>Station</th><th>District</th><th>Bikes</th><th>Docks</th><th>Rentals</th><th>Returns</th><th>Shortage</th><th>Full</th></tr></thead>
          <tbody>{''.join(station_rows)}</tbody>
        </table>
      </section>

      <section>
        <h2>OD Counts</h2>
        <ul class="od-list">{''.join(od_items)}</ul>
      </section>
    </div>

    <div id="animation-page" class="page">
      <section class="animation-shell">
        <div class="animation-panel">
          <div class="graph-toolbar">
            <h2 id="graph-title">行政區層級</h2>
            <button id="back-to-districts" class="back-button" type="button" hidden>返回行政區</button>
          </div>
          <div class="svg-wrap">
            <svg id="playback-graph" viewBox="0 0 900 520" role="img" aria-label="dynamic rider playback">
              <g id="route-layer"></g>
              <g id="station-layer"></g>
              <g id="rider-layer"></g>
            </svg>
          </div>
          <div class="legend">
            <span><span class="legend-dot" style="background:#4d8f5f"></span>同行政區 rider</span>
            <span><span class="legend-dot" style="background:#b66a3c"></span>跨行政區 rider</span>
            <span><span class="legend-dot" style="background:#c42f2f"></span>可借車比例接近 0%</span>
            <span><span class="legend-dot" style="background:#7fbf7b"></span>可借車比例約 50%</span>
            <span><span class="legend-dot" style="background:#6a3d9a"></span>可借車比例接近 100%</span>
            <span>node 內的 Q:n 代表滿站等待 queue 人數</span>
            <span>圓點位置代表該 rider 在起點與終點之間的旅程進度</span>
          </div>
        </div>

        <aside class="control-panel">
          <h2>Playback Control</h2>
          <div class="controls">
            <div class="control-row">
              <button id="play-button" class="primary" type="button">播放</button>
              <button id="reset-button" type="button">重設</button>
            </div>
            <label>
              模擬時間：<strong id="time-label">0.0 min</strong>
              <input id="time-slider" class="time-slider" type="range" min="0" max="{simulation_minutes}" step="0.1" value="0">
            </label>
            <div class="control-row" aria-label="time shortcuts">
              <button class="jump-button" type="button" data-time="0">開始</button>
              <button class="jump-button" type="button" data-time="{simulation_minutes * 0.25:.1f}">25%</button>
              <button class="jump-button" type="button" data-time="{simulation_minutes * 0.5:.1f}">50%</button>
              <button class="jump-button" type="button" data-time="{simulation_minutes * 0.75:.1f}">75%</button>
              <button class="jump-button" type="button" data-time="{simulation_minutes:.1f}">結束</button>
            </div>
            <label>
              播放速度：
              <select id="speed-select">
                <option value="5">5 模擬分鐘 / 秒</option>
                <option value="10">10 模擬分鐘 / 秒</option>
                <option value="30">30 模擬分鐘 / 秒</option>
                <option value="60" selected>60 模擬分鐘 / 秒</option>
                <option value="120">120 模擬分鐘 / 秒</option>
                <option value="240">240 模擬分鐘 / 秒</option>
                <option value="480">480 模擬分鐘 / 秒</option>
                <option value="720">720 模擬分鐘 / 秒</option>
              </select>
            </label>
            <div class="metric"><span>目前騎乘中</span><strong id="active-count">0</strong></div>
            <div class="metric"><span>已出發 rider</span><strong id="started-count">0</strong></div>
            <p class="note">這個動畫是依照離散事件紀錄重播，所以它不是重新模擬，而是把已經發生的 rider route_planned 事件視覺化。</p>
            <div>
              <strong>目前路上部分 rider</strong>
              <ul id="recent-routes" class="recent-routes"></ul>
            </div>
          </div>
        </aside>
      </section>
    </div>
  </main>
  <script>
    const routes = {animation_routes_json};
    const stationStates = {station_states_json};
    const queueIntervals = {waiting_intervals_json};
    const simulationMinutes = {simulation_minutes};
    const routeLayer = document.getElementById("route-layer");
    const stationLayer = document.getElementById("station-layer");
    const riderLayer = document.getElementById("rider-layer");
    const graphTitle = document.getElementById("graph-title");
    const backToDistricts = document.getElementById("back-to-districts");
    const playButton = document.getElementById("play-button");
    const resetButton = document.getElementById("reset-button");
    const timeSlider = document.getElementById("time-slider");
    const timeLabel = document.getElementById("time-label");
    const speedSelect = document.getElementById("speed-select");
    const activeCount = document.getElementById("active-count");
    const startedCount = document.getElementById("started-count");
    const recentRoutes = document.getElementById("recent-routes");

    let currentTime = 0;
    let isPlaying = false;
    let lastFrameTime = null;
    let graphView = "district";
    let selectedDistrict = null;

    const districts = Array.from(new Set(
      routes.flatMap((route) => [route.originDistrict, route.destinationDistrict])
    )).sort();
    const stationDistrict = new Map();
    routes.forEach((route) => {{
      stationDistrict.set(route.origin, route.originDistrict);
      stationDistrict.set(route.destination, route.destinationDistrict);
    }});
    const stationsByDistrict = new Map();
    stationDistrict.forEach((district, station) => {{
      if (!stationsByDistrict.has(district)) {{
        stationsByDistrict.set(district, []);
      }}
      stationsByDistrict.get(district).push(station);
    }});
    stationsByDistrict.forEach((stations) => stations.sort());
    const externalGatewayPosition = {{ x: 450, y: 260 }};

    function interpolateColor(colorA, colorB, ratio) {{
      const boundedRatio = Math.max(0, Math.min(1, ratio));
      const channel = (index) => Math.round(colorA[index] + (colorB[index] - colorA[index]) * boundedRatio);
      return `rgb(${{channel(0)}}, ${{channel(1)}}, ${{channel(2)}})`;
    }}

    function stockColor(ratio) {{
      const boundedRatio = Math.max(0, Math.min(1, ratio));
      const lowBad = [196, 47, 47];
      const healthy = [127, 191, 123];
      const highBad = [106, 61, 154];
      if (boundedRatio <= 0.5) {{
        return interpolateColor(lowBad, healthy, boundedRatio / 0.5);
      }}
      return interpolateColor(healthy, highBad, (boundedRatio - 0.5) / 0.5);
    }}

    function stationBikesAt(stationId, time) {{
      const state = stationStates[stationId];
      if (!state) {{
        return 0;
      }}
      const events = state.events || [];
      let left = 0;
      let right = events.length - 1;
      let eventIndex = -1;
      while (left <= right) {{
        const middle = Math.floor((left + right) / 2);
        if (events[middle].time <= time) {{
          eventIndex = middle;
          left = middle + 1;
        }} else {{
          right = middle - 1;
        }}
      }}
      return eventIndex >= 0 ? events[eventIndex].bikes : state.initialBikes;
    }}

    function stationBikeRatio(stationId, time) {{
      const state = stationStates[stationId];
      if (!state || !state.capacity) {{
        return 0;
      }}
      return stationBikesAt(stationId, time) / state.capacity;
    }}

    function districtBikeRatio(district, time) {{
      const stations = stationsByDistrict.get(district) || [];
      let bikes = 0;
      let capacity = 0;
      stations.forEach((station) => {{
        const state = stationStates[station];
        if (!state) {{
          return;
        }}
        bikes += stationBikesAt(station, time);
        capacity += state.capacity;
      }});
      return capacity > 0 ? bikes / capacity : 0;
    }}

    function svgElement(name, attributes) {{
      const element = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
      return element;
    }}

    function clearGraph() {{
      routeLayer.replaceChildren();
      stationLayer.replaceChildren();
      riderLayer.replaceChildren();
    }}

    function circularLayout(items, centerX, centerY, radius) {{
      const positions = new Map();
      if (items.length === 1) {{
        positions.set(items[0], {{ x: centerX, y: centerY }});
        return positions;
      }}
      items.forEach((item, index) => {{
        const angle = -Math.PI / 2 + (2 * Math.PI * index) / items.length;
        positions.set(item, {{
          x: centerX + Math.cos(angle) * radius,
          y: centerY + Math.sin(angle) * radius
        }});
      }});
      return positions;
    }}

    function pairKey(a, b) {{
      return [a, b].sort().join("::");
    }}

    function drawLine(origin, destination, className = "route-line") {{
      routeLayer.appendChild(svgElement("line", {{
        class: className,
        x1: origin.x,
        y1: origin.y,
        x2: destination.x,
        y2: destination.y
      }}));
    }}

    function formatQueueCount(queueCount) {{
      return queueCount > 99 ? "Q:99+" : `Q:${{queueCount}}`;
    }}

    function drawNode(id, position, radius, nodeType, queueCountForNode, bikeRatio = null) {{
      const group = svgElement("g", {{
        class: "node-group",
        tabindex: "0",
        role: "button",
        "aria-label": id
      }});
      group.appendChild(svgElement("circle", {{
        class: `node-circle ${{nodeType}}-node`,
        cx: position.x,
        cy: position.y,
        r: radius,
        style: bikeRatio === null ? "" : `fill: ${{stockColor(bikeRatio)}};`
      }}));
      const label = svgElement("text", {{
        class: "station-label",
        x: position.x,
        y: position.y - 14
      }});
      label.textContent = id;
      group.appendChild(label);

      const countLabel = svgElement("text", {{
        class: "node-count",
        x: position.x,
        y: position.y + 2
      }});
      countLabel.textContent = formatQueueCount(queueCountForNode);
      group.appendChild(countLabel);

      if (bikeRatio !== null) {{
        const ratioLabel = svgElement("text", {{
          class: "node-ratio",
          x: position.x,
          y: position.y + 18
        }});
        ratioLabel.textContent = `${{Math.round(bikeRatio * 100)}}%`;
        group.appendChild(ratioLabel);
      }}

      if (nodeType === "district") {{
        group.addEventListener("click", () => {{
          graphView = "station";
          selectedDistrict = id;
          renderFrame();
        }});
        group.addEventListener("keydown", (event) => {{
          if (event.key === "Enter" || event.key === " ") {{
            graphView = "station";
            selectedDistrict = id;
            renderFrame();
          }}
        }});
      }}
      stationLayer.appendChild(group);
    }}

    function interpolate(origin, destination, progress) {{
      return {{
        x: origin.x + (destination.x - origin.x) * progress,
        y: origin.y + (destination.y - origin.y) * progress
      }};
    }}

    function perpendicularOffset(origin, destination, amount) {{
      const dx = destination.x - origin.x;
      const dy = destination.y - origin.y;
      const length = Math.sqrt(dx * dx + dy * dy) || 1;
      return {{
        x: (-dy / length) * amount,
        y: (dx / length) * amount
      }};
    }}

    function drawExternalGateway(inboundCount) {{
      stationLayer.appendChild(svgElement("circle", {{
        class: "external-node",
        cx: externalGatewayPosition.x,
        cy: externalGatewayPosition.y,
        r: 31
      }}));
      const label = svgElement("text", {{
        class: "station-label",
        x: externalGatewayPosition.x,
        y: externalGatewayPosition.y - 9
      }});
      label.textContent = "區外";
      stationLayer.appendChild(label);

      const inboundLabel = svgElement("text", {{
        class: "external-count",
        x: externalGatewayPosition.x,
        y: externalGatewayPosition.y + 10
      }});
      inboundLabel.textContent = `進入 ${{inboundCount}}`;
      stationLayer.appendChild(inboundLabel);
    }}

    function queueCountAt(stationId, time) {{
      return queueIntervals.filter((interval) => (
        interval.station === stationId &&
        interval.start <= time &&
        time < interval.end
      )).length;
    }}

    function queueCountForDistrict(district, time) {{
      const stations = stationsByDistrict.get(district) || [];
      return stations.reduce((total, stationId) => total + queueCountAt(stationId, time), 0);
    }}

    function drawRider(route, position, index, offsetVector = {{ x: 0, y: 0 }}) {{
      const jitter = ((index % 5) - 2) * 2;
      riderLayer.appendChild(svgElement("circle", {{
        class: "rider",
        cx: position.x + offsetVector.x,
        cy: position.y + offsetVector.y + jitter,
        r: 7,
        fill: route.sameDistrict ? "#4d8f5f" : "#b66a3c"
      }}));
    }}

    function formatTime(minutes) {{
      const day = Math.floor(minutes / 1440) + 1;
      const minuteOfDay = Math.floor(minutes % 1440);
      const hour = Math.floor(minuteOfDay / 60);
      const minute = minuteOfDay % 60;
      return `Day ${{day}}, ${{String(hour).padStart(2, "0")}}:${{String(minute).padStart(2, "0")}} (${{minutes.toFixed(1)}} min)`;
    }}

    function renderFrame() {{
      const activeRoutes = routes.filter((route) => route.start <= currentTime && currentTime <= route.end);
      const startedRoutes = routes.filter((route) => route.start <= currentTime);
      clearGraph();

      let visibleRoutes = [];
      if (graphView === "district") {{
        graphTitle.textContent = "行政區層級";
        backToDistricts.hidden = true;
        const districtPositions = circularLayout(districts, 450, 260, 180);
        const drawnPairs = new Set();
        routes.filter((route) => !route.sameDistrict).forEach((route) => {{
          const key = pairKey(route.originDistrict, route.destinationDistrict);
          if (drawnPairs.has(key)) {{
            return;
          }}
          drawnPairs.add(key);
          drawLine(
            districtPositions.get(route.originDistrict),
            districtPositions.get(route.destinationDistrict)
          );
        }});

        visibleRoutes = activeRoutes.filter((route) => !route.sameDistrict);
        districts.forEach((district) => {{
          drawNode(
            district,
            districtPositions.get(district),
            34,
            "district",
            queueCountForDistrict(district, currentTime),
            districtBikeRatio(district, currentTime)
          );
        }});
        visibleRoutes.slice(0, 250).forEach((route, index) => {{
          const origin = districtPositions.get(route.originDistrict);
          const destination = districtPositions.get(route.destinationDistrict);
          if (!origin || !destination) {{
            return;
          }}
          const progress = Math.max(0, Math.min(1, (currentTime - route.start) / route.duration));
          const sideOffset = perpendicularOffset(origin, destination, 16 + (index % 3) * 4);
          drawRider(route, interpolate(origin, destination, progress), index, sideOffset);
        }});
      }} else {{
        const stations = stationsByDistrict.get(selectedDistrict) || [];
        graphTitle.textContent = `${{selectedDistrict}} 站點層級`;
        backToDistricts.hidden = false;
        const stationPositions = circularLayout(stations, 450, 260, 180);
        const drawnPairs = new Set();
        routes.filter((route) => (
          route.sameDistrict &&
          route.originDistrict === selectedDistrict &&
          route.destinationDistrict === selectedDistrict
        )).forEach((route) => {{
          const key = pairKey(route.origin, route.destination);
          if (drawnPairs.has(key)) {{
            return;
          }}
          drawnPairs.add(key);
          drawLine(stationPositions.get(route.origin), stationPositions.get(route.destination));
        }});
        routes.filter((route) => (
          !route.sameDistrict &&
          (route.originDistrict === selectedDistrict || route.destinationDistrict === selectedDistrict)
        )).forEach((route) => {{
          const stationId = route.originDistrict === selectedDistrict ? route.origin : route.destination;
          if (!stationPositions.has(stationId)) {{
            return;
          }}
          drawLine(stationPositions.get(stationId), externalGatewayPosition, "route-line external-line");
        }});

        visibleRoutes = activeRoutes.filter((route) => (
          route.sameDistrict &&
          route.originDistrict === selectedDistrict &&
          route.destinationDistrict === selectedDistrict
        ));
        const externalRoutes = activeRoutes.filter((route) => (
          !route.sameDistrict &&
          (route.originDistrict === selectedDistrict || route.destinationDistrict === selectedDistrict)
        ));
        const inboundExternalCount = externalRoutes.filter((route) => (
          route.destinationDistrict === selectedDistrict
        )).length;
        visibleRoutes = visibleRoutes.concat(externalRoutes);
        stations.forEach((station) => {{
          const stationPosition = stationPositions.get(station);
          drawNode(
            station,
            stationPosition,
            34,
            "station",
            queueCountAt(station, currentTime),
            stationBikeRatio(station, currentTime)
          );
        }});
        drawExternalGateway(inboundExternalCount);
        visibleRoutes.slice(0, 250).forEach((route, index) => {{
          const origin = route.originDistrict === selectedDistrict
            ? stationPositions.get(route.origin)
            : externalGatewayPosition;
          const destination = route.destinationDistrict === selectedDistrict
            ? stationPositions.get(route.destination)
            : externalGatewayPosition;
          if (!origin || !destination) {{
            return;
          }}
          const progress = Math.max(0, Math.min(1, (currentTime - route.start) / route.duration));
          const offset = route.sameDistrict
            ? {{ x: 0, y: 0 }}
            : perpendicularOffset(origin, destination, 10 + (index % 3) * 3);
          drawRider(route, interpolate(origin, destination, progress), index, offset);
        }});
      }}

      timeSlider.value = currentTime;
      timeLabel.textContent = formatTime(currentTime);
      activeCount.textContent = activeRoutes.length;
      startedCount.textContent = startedRoutes.length;
      recentRoutes.innerHTML = visibleRoutes.slice(0, 12).map((route) => (
        `<li><strong>${{route.riderId}}</strong>：${{route.origin}} → ${{route.destination}}，剩餘 ${{Math.max(0, route.end - currentTime).toFixed(1)}} min</li>`
      )).join("");
    }}

    function animationLoop(timestamp) {{
      if (!isPlaying) {{
        lastFrameTime = null;
        return;
      }}
      if (lastFrameTime === null) {{
        lastFrameTime = timestamp;
      }}
      const elapsedSeconds = (timestamp - lastFrameTime) / 1000;
      lastFrameTime = timestamp;
      currentTime = Math.min(simulationMinutes, currentTime + elapsedSeconds * Number(speedSelect.value));
      if (currentTime >= simulationMinutes) {{
        isPlaying = false;
        playButton.textContent = "播放";
      }}
      renderFrame();
      requestAnimationFrame(animationLoop);
    }}

    document.querySelectorAll(".tab-button").forEach((button) => {{
      button.addEventListener("click", () => {{
        document.querySelectorAll(".tab-button").forEach((item) => item.classList.remove("active"));
        document.querySelectorAll(".page").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        document.getElementById(button.dataset.page).classList.add("active");
        renderFrame();
      }});
    }});

    playButton.addEventListener("click", () => {{
      isPlaying = !isPlaying;
      playButton.textContent = isPlaying ? "暫停" : "播放";
      if (isPlaying) {{
        requestAnimationFrame(animationLoop);
      }}
    }});

    resetButton.addEventListener("click", () => {{
      isPlaying = false;
      currentTime = 0;
      graphView = "district";
      selectedDistrict = null;
      playButton.textContent = "播放";
      renderFrame();
    }});

    timeSlider.addEventListener("input", () => {{
      currentTime = Number(timeSlider.value);
      renderFrame();
    }});

    document.querySelectorAll(".jump-button").forEach((button) => {{
      button.addEventListener("click", () => {{
        currentTime = Number(button.dataset.time);
        renderFrame();
      }});
    }});

    backToDistricts.addEventListener("click", () => {{
      graphView = "district";
      selectedDistrict = null;
      renderFrame();
    }});

    renderFrame();
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    """解析命令列參數。"""

    parser = argparse.ArgumentParser(description="Visualize a simple scenario report.")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="specific report folder; default uses latest simple_scenario report",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=PROJECT_ROOT / "report",
        help="root folder containing simple_scenario reports",
    )
    return parser.parse_args()


def main() -> None:
    """讀取報告並輸出 visualization.html。"""

    args = parse_args()
    report_dir = args.report_dir or latest_report(args.report_root)
    summary = json.loads(report_dir.joinpath("summary.json").read_text(encoding="utf-8"))
    routes = load_animation_routes(report_dir)
    station_states = load_station_state_history(report_dir, summary)
    waiting_intervals = load_waiting_intervals(report_dir)
    html = render_html(summary, routes, station_states, waiting_intervals, report_dir)
    output_path = report_dir / "visualization.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"Visualization written to: {output_path}")


if __name__ == "__main__":
    main()
