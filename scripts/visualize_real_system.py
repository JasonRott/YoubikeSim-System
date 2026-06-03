"""產生真實系統地圖式 HTML 視覺化。

此版本重點是提供大畫布 pan/zoom、真實座標、短 ID 與分層檢視。
它不是 GIS 地圖，也不載入外部底圖；所有座標都來自
`data/processed/visualization_inputs`。

Phase 7 更新：
- edge 改為直線（不再曲線偏移）。
- node 顏色與 % 改為依播放時間即時計算（從 events.csv 重建每站時序佔用率）。
- rider 經過 node 時不再被完全覆蓋，改為明顯降低透明度。
- 採用 simple scenario 視覺風格（深藍標題、卡片面板、色彩圖例）。
"""

from __future__ import annotations

import argparse
import csv
from html import escape
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def latest_real_report(report_root: Path) -> Path:
    candidates = [
        path
        for path in report_root.rglob("real_system_*")
        if path.is_dir() and path.joinpath("summary.json").exists()
    ]
    if not candidates:
        raise FileNotFoundError(f"No real_system reports found in {report_root}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_routes(report_dir: Path, max_routes: int) -> list[dict[str, Any]]:
    """從 route_planned 事件載入可播放路線。

    路線過多時，採「全天時間分層抽樣」而非只取最前面 N 筆——否則長時模擬只會在
    最初幾小時看到 rider、之後完全沒有（即使站點顏色整天在變）。events.csv 依時間排序，
    故等距抽樣即等同於時間分層抽樣，可讓 rider 平均分布在整段模擬時間。
    """

    all_routes: list[dict[str, Any]] = []
    with (report_dir / "events.csv").open("r", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if row.get("event_type") != "route_planned":
                continue
            start = float(row["time"])
            duration = float(row["total_travel_time"])
            all_routes.append(
                {
                    "riderId": row["rider_id"],
                    "origin": row["origin_station_id"],
                    "destination": row["destination_station_id"],
                    "originDistrict": row["origin_district_id"],
                    "destinationDistrict": row["destination_district_id"],
                    "start": start,
                    "end": start + duration,
                    "duration": duration,
                    "sameDistrict": row["origin_district_id"]
                    == row["destination_district_id"],
                }
            )

    if len(all_routes) <= max_routes:
        return all_routes
    step = len(all_routes) / max_routes
    return [all_routes[int(i * step)] for i in range(max_routes)]


def build_station_state_history(
    report_dir: Path,
    summary: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """從所有「改變站點車數」的事件重建每站隨時間變化的可借車數。

    events.csv 只記錄事件後的 bikes_after；為得到 time=0 的初始狀態，從模擬結束狀態
    反向扣除所有事件的變化量。**必須涵蓋調度卡車的補/抽車**（truck_replenish / truck_withdraw），
    否則重建出來的初始車數會錯（可能為負），且卡車到站後站點顏色不會更新。
    各事件對「可借車數」的變化量 delta：rental −1、return +1、truck_replenish +qty、truck_withdraw −qty。
    initialBikes = finalBikes − Σdelta；時間線則用各事件的 bikes_after（皆為當下真實值）。
    """

    states: dict[str, dict[str, Any]] = {}
    for snapshot in summary["station_snapshots"]:
        station_id = str(snapshot["station_id"])
        states[station_id] = {
            "capacity": int(snapshot["capacity"]),
            "finalBikes": int(snapshot["available_bikes"]),
            "events": [],
        }

    # 每種事件對可借車數的變化量；truck 事件帶 qty。
    def event_delta(event_type: str, qty: int) -> int:
        if event_type == "rental":
            return -1
        if event_type == "return":
            return 1
        if event_type == "truck_replenish":
            return qty
        if event_type == "truck_withdraw":
            return -qty
        return 0

    changing = {"rental", "return", "truck_replenish", "truck_withdraw"}
    station_events: dict[str, list[dict[str, Any]]] = {
        station_id: [] for station_id in states
    }
    with (report_dir / "events.csv").open("r", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            event_type = row.get("event_type")
            if event_type not in changing:
                continue
            station_id = row.get("station_id")
            bikes_after = row.get("bikes_after")
            if not station_id or bikes_after in (None, ""):
                continue
            station_id = str(station_id)
            if station_id not in station_events:
                continue
            qty_raw = row.get("qty")
            qty = int(float(qty_raw)) if qty_raw not in (None, "") else 1
            station_events[station_id].append(
                {
                    "time": float(row["time"]),
                    "delta": event_delta(event_type, qty),
                    "bikes": int(float(bikes_after)),
                }
            )

    for station_id, events in station_events.items():
        events.sort(key=lambda event: event["time"])
        # 反向扣除所有變化量得到初始車數。
        initial_bikes = states[station_id]["finalBikes"] - sum(e["delta"] for e in events)
        states[station_id]["initialBikes"] = initial_bikes
        states[station_id]["events"] = [
            {"time": event["time"], "bikes": event["bikes"]} for event in events
        ]

    for state in states.values():
        state.setdefault("initialBikes", state["finalBikes"])

    return states


def render_html(
    report_dir: Path,
    summary: dict[str, Any],
    routes: list[dict[str, Any]],
    station_positions: dict[str, dict[str, Any]],
    district_positions: dict[str, dict[str, Any]],
    canvas_metadata: dict[str, Any],
    station_states: dict[str, dict[str, Any]],
    district_boundaries: dict[str, Any],
    dispatch_data: dict[str, Any] | None = None,
) -> str:
    routes_json = json.dumps(routes, ensure_ascii=False)
    station_positions_json = json.dumps(station_positions, ensure_ascii=False)
    district_positions_json = json.dumps(district_positions, ensure_ascii=False)
    canvas_json = json.dumps(canvas_metadata, ensure_ascii=False)
    station_states_json = json.dumps(station_states, ensure_ascii=False)
    district_boundaries_json = json.dumps(district_boundaries, ensure_ascii=False)
    dispatch_json = json.dumps(dispatch_data, ensure_ascii=False)
    summary_json = json.dumps(
        {
            "simulationMinutes": summary["simulation_minutes"],
            "stationCount": summary["station_count"],
            "districtCount": summary["district_count"],
            "arrivals": summary["observed_total_arrivals"],
            "routes": summary["successful_routes"],
            "full": summary["full_station_events"],
            "shortage": summary["shortage_events"],
            "profile": summary["profile"],
        },
        ensure_ascii=False,
    )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Real System Map Visualization</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Arial, "Microsoft JhengHei", sans-serif;
      background: #f5f7f8;
      color: #1d1d1f;
    }}
    body {{
      margin: 0;
    }}
    header {{
      padding: 20px 28px 16px;
      background: #233043;
      color: white;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 22px;
    }}
    .meta {{
      color: #c7d2dd;
      font-size: 13px;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      padding: 12px 28px;
      background: #ffffff;
      border-bottom: 1px solid #dfe5ea;
    }}
    button, select {{
      border: 1px solid #b8c4ce;
      background: white;
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      font-family: inherit;
      cursor: pointer;
    }}
    button.primary {{
      background: #233043;
      color: white;
      border-color: #233043;
    }}
    label {{
      font-size: 13px;
      color: #41505f;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .time-control {{
      display: flex;
      align-items: center;
      gap: 6px;
      padding-left: 4px;
    }}
    .metric {{
      background: #f7fafc;
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 12px;
      color: #41505f;
    }}
    .metric strong {{
      color: #1d1d1f;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
      padding: 9px 28px;
      background: #eef2f5;
      border-bottom: 1px solid #dfe5ea;
      font-size: 12.5px;
      color: #41505f;
    }}
    .legend-dot {{
      display: inline-block;
      width: 11px;
      height: 11px;
      border-radius: 50%;
      margin-right: 5px;
      vertical-align: -1px;
    }}
    .legend-bar {{
      display: inline-block;
      width: 120px;
      height: 11px;
      border-radius: 999px;
      vertical-align: -1px;
      margin-right: 5px;
      background: linear-gradient(to right, #c42f2f, #7fbf7b, #6a3d9a);
    }}
    .map-shell {{
      height: calc(100vh - 196px);
      min-height: 520px;
      overflow: hidden;
      background: #fbfcfd;
      position: relative;
      cursor: grab;
    }}
    .map-shell.dragging {{
      cursor: grabbing;
    }}
    svg {{
      width: 100%;
      height: 100%;
      display: block;
      background: #fbfcfd;
    }}
    /* 站點層級的地理鄰近骨架：最淺的灰，純背景。 */
    .mesh-edge {{
      fill: none;
      stroke: #dde4ea;
      stroke-width: 1;
      stroke-opacity: 0.8;
    }}
    /* 行政區界線：較深的中性灰、不填色，作為地圖底圖。 */
    .district-boundary {{
      fill: none;
      stroke: #7d8a98;
      stroke-width: 1.6;
      stroke-opacity: 0.9;
      vector-effect: non-scaling-stroke;
      pointer-events: none;
    }}
    /* 行政區之間的 OD 流量線：用藍色，明確區別於灰色行政區界線。 */
    .district-edge {{
      fill: none;
      stroke: #2f78b5;
      stroke-width: 2.4;
      stroke-opacity: 0.75;
    }}
    /* 線寬固定為螢幕像素，縮放時不變粗。node/rider 的大小則由各自的 counter-scale 控制。 */
    .district-edge, .mesh-edge, .rider,
    .station-node, .district-node {{
      vector-effect: non-scaling-stroke;
    }}
    .station-node, .district-node {{
      stroke: #233043;
      stroke-width: 2;
    }}
    .district-node {{
      cursor: pointer;
    }}
    .node-label {{
      font-weight: 800;
      fill: #111827;
      stroke: white;
      stroke-width: 3px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    .node-sub {{
      font-weight: 700;
      fill: #111827;
      stroke: white;
      stroke-width: 2px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    .rider {{
      stroke: rgba(255, 255, 255, 0.85);
      stroke-width: 0.8;
    }}
    .truck {{
      stroke: #14324a;
      stroke-width: 1.4;
      vector-effect: non-scaling-stroke;
    }}
    .truck-cab {{
      fill: #0f273a;
      vector-effect: non-scaling-stroke;
    }}
    /* 母車：夜間跨區結算的大車（深藍底、金色粗框），與白天 P7 小卡車區隔。 */
    .truck.mother {{
      stroke: #ffce3a;
      stroke-width: 2.4;
      vector-effect: non-scaling-stroke;
    }}
    .truck-mother-label {{
      font-weight: 800;
      fill: #ffffff;
      stroke: #16244a;
      stroke-width: 2px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    .depot-box {{
      stroke: #8a5a12;
      stroke-width: 2;
      vector-effect: non-scaling-stroke;
    }}
    .depot-roof {{
      fill: #b5701a;
      stroke: #8a5a12;
      stroke-width: 2;
      vector-effect: non-scaling-stroke;
    }}
    .depot-label {{
      font-weight: 800;
      fill: #5b3b08;
      stroke: white;
      stroke-width: 3px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    .depot-sub, .depot-pct {{
      font-weight: 800;
      fill: #3a2705;
      stroke: white;
      stroke-width: 2.5px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    /* 集散場「母車駐留」標示：夜間母車抵達後顯示，金底深藍字。 */
    .depot-mother {{
      font-weight: 800;
      fill: #16244a;
      stroke: #ffce3a;
      stroke-width: 3px;
      paint-order: stroke fill;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    .note {{
      position: absolute;
      left: 14px;
      bottom: 12px;
      background: rgba(255,255,255,0.94);
      border: 1px solid #dfe5ea;
      border-radius: 8px;
      padding: 8px 11px;
      font-size: 12px;
      color: #59636f;
      max-width: 560px;
    }}
    .hidden {{
      display: none;
    }}
    input[type="range"] {{
      width: 200px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Real System Map Visualization</h1>
    <div class="meta">{escape(str(report_dir))}</div>
  </header>
  <div class="toolbar">
    <button id="district-view" class="primary" type="button">行政區層級</button>
    <button id="station-view" type="button">站點層級</button>
    <button id="zoom-in" type="button">放大</button>
    <button id="zoom-out" type="button">縮小</button>
    <button id="reset-view" type="button">重設視窗</button>
    <label>行政區
      <select id="district-select"></select>
    </label>
    <div class="time-control">
      <button id="play-pause" type="button">播放</button>
      <label>速度
        <select id="speed-select">
          <option value="0.5">0.5 分/秒</option>
          <option value="1" selected>1 分/秒</option>
          <option value="2">2 分/秒</option>
          <option value="5">5 分/秒</option>
          <option value="10">10 分/秒</option>
          <option value="30">30 分/秒</option>
          <option value="60">60 分/秒</option>
        </select>
      </label>
      <label>時間
        <input id="time-slider" type="range" min="0" max="{summary['simulation_minutes']}" step="0.1" value="0">
      </label>
    </div>
    <span class="metric" id="time-label">0.0 min</span>
    <span class="metric">Profile: <strong>{escape(str(summary['profile']))}</strong></span>
    <span class="metric">Stations: <strong>{summary['station_count']}</strong></span>
    <span class="metric">Routes: <strong>{len(routes)}</strong></span>
    <span class="metric">騎乘中 <strong id="active-count">0</strong></span>
    <span class="metric" id="dispatch-metric">調度 —</span>
  </div>
  <div class="legend">
    <span><span class="legend-dot" style="background:#4d8f5f"></span>同行政區 rider</span>
    <span><span class="legend-dot" style="background:#b66a3c"></span>跨行政區 rider</span>
    <span><span class="legend-bar"></span>可借車比例：0% → 50% → 100%</span>
    <span><span class="legend-dot" style="background:#156599;border-radius:2px"></span>白天 P7 卡車（顏色＝車上腳踏車比例，淺→滿）</span>
    <span><span class="legend-dot" style="background:#16244a;border-radius:2px;border:1.5px solid #ffce3a"></span>母車（夜間跨區結算；較大、深藍底＋「母」字）</span>
    <span><span class="legend-dot" style="background:#d68a16;border-radius:2px"></span>集散站（顏色＝庫存比例；標籤 🚚卡車數 🚲腳踏車數）</span>
    <span>node 內數字為該時刻可借車比例；rider 經過 node 時會變半透明，代表只是路過。</span>
  </div>
  <div id="map-shell" class="map-shell">
    <svg id="map-svg" role="img" aria-label="real system map">
      <g id="viewport">
        <g id="boundary-layer"></g>
        <g id="edge-layer"></g>
        <g id="node-layer"></g>
        <g id="rider-layer"></g>
        <g id="depot-layer"></g>
        <g id="truck-layer"></g>
      </g>
    </svg>
    <div class="note">拖曳可以平移，滑鼠滾輪或按鈕可以縮放。node 顏色與 % 會隨播放時間變化。站點層級會畫出全部站點，重設會聚焦目前選定的行政區（可用拉桿切換）。灰色細線是地理鄰近骨架（非行駛路徑）、灰色粗線是行政區界線；實際借車流量由 rider 直線往返真實起訖站點呈現。</div>
  </div>
  <script>
    const routes = {routes_json};
    const stationPositions = {station_positions_json};
    const districtPositions = {district_positions_json};
    const canvasMeta = {canvas_json};
    const stationStates = {station_states_json};
    const districtBoundaries = {district_boundaries_json};
    const dispatchData = {dispatch_json};
    const summary = {summary_json};

    const svg = document.getElementById("map-svg");
    const shell = document.getElementById("map-shell");
    const viewport = document.getElementById("viewport");
    const boundaryLayer = document.getElementById("boundary-layer");
    const edgeLayer = document.getElementById("edge-layer");
    const nodeLayer = document.getElementById("node-layer");
    const riderLayer = document.getElementById("rider-layer");
    const depotLayer = document.getElementById("depot-layer");
    const truckLayer = document.getElementById("truck-layer");
    const districtSelect = document.getElementById("district-select");
    const timeSlider = document.getElementById("time-slider");
    const timeLabel = document.getElementById("time-label");
    const activeCountLabel = document.getElementById("active-count");
    const districtViewButton = document.getElementById("district-view");
    const stationViewButton = document.getElementById("station-view");
    const playPauseButton = document.getElementById("play-pause");
    const speedSelect = document.getElementById("speed-select");
    const dispatchMetricLabel = document.getElementById("dispatch-metric");

    // 工具列調度指標：在路上的卡車數，以及集散站在庫率（站點層級看選定區，行政區層級看全市）。
    function updateDispatchMetric() {{
      if (!dispatchMetricLabel) return;
      if (!hasDispatch) {{ dispatchMetricLabel.textContent = "調度：無（baseline）"; return; }}
      let onRoad = 0;
      truckLegsById.forEach((legs) => {{
        const s = truckStateAt(legs, currentTime);
        if (s && !s.atDepot) onRoad += 1;
      }});
      let ratio = null;
      if (viewMode === "station") {{
        ratio = depotParkedRatio(selectedDistrict, currentTime);
      }} else {{
        let inv = 0, tot = 0, ok = false;
        Object.keys(depotPos).forEach((d) => {{
          const r = depotParkedRatio(d, currentTime);
          const i = depotInventoryAt(d, currentTime);
          if (r !== null && i !== null) {{ ok = true; inv += i; tot += (r > 0 ? i / r : i); }}
        }});
        ratio = ok && tot > 0 ? inv / tot : null;
      }}
      const ratioText = ratio === null ? "—" : `${{Math.round(ratio * 100)}}%`;
      const scope = viewMode === "station" ? selectedDistrict : "全市";
      dispatchMetricLabel.textContent = `卡車在路上 ${{onRoad}}｜${{scope}}集散站在庫率 ${{ratioText}}`;
    }}

    // 預先把每個行政區的站點列表整理好，供 districtBikeRatio 使用。
    const stationsByDistrict = new Map();
    Object.entries(stationPositions).forEach(([stationId, pos]) => {{
      if (!stationsByDistrict.has(pos.district)) stationsByDistrict.set(pos.district, []);
      stationsByDistrict.get(pos.district).push(stationId);
    }});

    // 多邊形面積中心（centroid）。供行政區 node 放在地理中心，視覺較平衡。
    function ringCentroid(ring) {{
      let area = 0;
      let cx = 0;
      let cy = 0;
      for (let i = 0; i < ring.length; i += 1) {{
        const x0 = ring[i][0];
        const y0 = ring[i][1];
        const x1 = ring[(i + 1) % ring.length][0];
        const y1 = ring[(i + 1) % ring.length][1];
        const cross = x0 * y1 - x1 * y0;
        area += cross;
        cx += (x0 + x1) * cross;
        cy += (y0 + y1) * cross;
      }}
      area *= 0.5;
      if (Math.abs(area) < 1e-6) {{
        let sx = 0;
        let sy = 0;
        ring.forEach((point) => {{ sx += point[0]; sy += point[1]; }});
        return {{ x: sx / ring.length, y: sy / ring.length }};
      }}
      return {{ x: cx / (6 * area), y: cy / (6 * area) }};
    }}

    // 行政區 node 顯示位置：有邊界者用邊界面積中心（地理中心）；
    // 無邊界者（如臺大公館校區）退回站點重心。
    const districtNodePos = {{}};
    Object.keys(districtPositions).forEach((name) => {{
      const rings = districtBoundaries[name];
      if (rings && rings.length) {{
        const mainRing = rings.reduce((a, b) => (b.length > a.length ? b : a));
        districtNodePos[name] = ringCentroid(mainRing);
      }} else {{
        districtNodePos[name] = {{ x: districtPositions[name].x, y: districtPositions[name].y }};
      }}
    }});

    // ---- 調度資料預處理（集散站 + 卡車軌跡 + 庫存時間線）----
    const hasDispatch = !!(dispatchData && dispatchData.truckLegs && dispatchData.truckLegs.length);
    const truckCapacity = (dispatchData && dispatchData.truckCapacity) || 30;
    // 集散站在 canvas 上的位置（站點 x/y 幾何中位數；無則退回行政區 node 位置）。
    const depotPos = {{}};
    const depotCapacity = {{}};
    if (dispatchData && dispatchData.depots) {{
      dispatchData.depots.forEach((dep) => {{
        const x = (dep.x !== null && dep.x !== undefined) ? dep.x
          : (districtNodePos[dep.district] ? districtNodePos[dep.district].x : 0);
        const y = (dep.y !== null && dep.y !== undefined) ? dep.y
          : (districtNodePos[dep.district] ? districtNodePos[dep.district].y : 0);
        depotPos[dep.district] = {{ x, y }};
        depotCapacity[dep.district] = dep.capacity || 300;
      }});
    }}
    // 每台卡車的軌跡段（依 t0 排序），與每座集散站的庫存階梯（依 time 排序）。
    const truckLegsById = new Map();
    if (dispatchData && dispatchData.truckLegs) {{
      dispatchData.truckLegs.forEach((leg) => {{
        if (!truckLegsById.has(leg.truckId)) truckLegsById.set(leg.truckId, []);
        truckLegsById.get(leg.truckId).push(leg);
      }});
      truckLegsById.forEach((legs) => legs.sort((a, b) => a.t0 - b.t0));
    }}
    const depotTimelineById = new Map();
    if (dispatchData && dispatchData.depotTimeline) {{
      dispatchData.depotTimeline.forEach((e) => {{
        if (!depotTimelineById.has(e.depot)) depotTimelineById.set(e.depot, []);
        depotTimelineById.get(e.depot).push(e);
      }});
      depotTimelineById.forEach((arr) => arr.sort((a, b) => a.time - b.time));
    }}

    // 母車駐留：每台母車「抵達(t1)」後駐留赤字區集散場，直到該夜窗結束(04:00)＝母車收工。
    // 供集散場顯示「母×N」標示（與午夜庫存逐台累加一致）。NIGHT_WINDOW = 240 分（00:00→04:00）。
    const NIGHT_WINDOW_MIN = 240;
    const motherResidentByDepot = new Map();
    if (dispatchData && dispatchData.truckLegs) {{
      dispatchData.truckLegs.forEach((leg) => {{
        if (leg.kind === "mother" && leg.moving) {{
          const dayBase = Math.floor(leg.t0 / 1440) * 1440;
          const arr = motherResidentByDepot.get(leg.toId) || [];
          arr.push({{ t1: leg.t1, until: dayBase + NIGHT_WINDOW_MIN, load: leg.load }});
          motherResidentByDepot.set(leg.toId, arr);
        }}
      }});
    }}
    // 某集散場此刻駐留的母車數與其載車總數（抵達後、夜窗結束前）。
    function mothersAtDepot(district, time) {{
      const arr = motherResidentByDepot.get(district);
      if (!arr) return {{ n: 0, bikes: 0 }};
      let n = 0, bikes = 0;
      arr.forEach((a) => {{ if (time >= a.t1 && time < a.until) {{ n += 1; bikes += a.load; }} }});
      return {{ n, bikes }};
    }}

    // 解析一個地點（depot/station）到 canvas 座標。
    function locToXY(type, id) {{
      if (type === "depot") return depotPos[id] || null;
      return stationPositions[id] || null;
    }}
    // 集散站在某時刻的庫存（階梯函式，二分搜尋）。
    function depotInventoryAt(district, time) {{
      const arr = depotTimelineById.get(district);
      if (!arr || !arr.length) return null;
      let lo = 0, hi = arr.length - 1, idx = 0;
      while (lo <= hi) {{ const m = (lo + hi) >> 1; if (arr[m].time <= time) {{ idx = m; lo = m + 1; }} else hi = m - 1; }}
      return arr[idx].inventory;
    }}
    // 卡車在某時刻的狀態：位置、載量、朝向、是否在集散站。null = 該時刻無資料。
    function truckStateAt(legs, time) {{
      if (!legs || !legs.length) return null;
      if (time < legs[0].t0) return null;
      // 找最後一個 t0 <= time 的段。
      let lo = 0, hi = legs.length - 1, idx = 0;
      while (lo <= hi) {{ const m = (lo + hi) >> 1; if (legs[m].t0 <= time) {{ idx = m; lo = m + 1; }} else hi = m - 1; }}
      const leg = legs[idx];
      // 母車的端點帶顯式 fromXY/toXY（行政區中心可能無 depot/station 座標）；優先採用。
      const a = leg.fromXY || locToXY(leg.fromType, leg.fromId);
      const b = leg.toXY || locToXY(leg.toType, leg.toId);
      if (!a || !b) return null;
      let pos, angle = 0;
      if (leg.moving && leg.t1 > leg.t0) {{
        const p = Math.max(0, Math.min(1, (time - leg.t0) / (leg.t1 - leg.t0)));
        pos = {{ x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p }};
        angle = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;
      }} else {{
        pos = {{ x: b.x, y: b.y }};
      }}
      const atDepot = (!leg.moving && leg.toType === "depot");
      return {{ pos, angle, load: leg.load, cap: leg.cap, atDepot, kind: leg.kind || "day", depotId: leg.toType === "depot" ? leg.toId : null }};
    }}

    let viewMode = "district";
    let selectedDistrict = Object.keys(districtPositions).sort()[0];
    let currentTime = 0;
    let transform = {{ x: 0, y: 0, scale: 1 }};
    let baseFitScale = 1;
    let dragging = false;
    let dragStart = {{ x: 0, y: 0 }};
    let dragTransformStart = {{ x: 0, y: 0 }};
    let isPlaying = false;
    let lastFrameTime = null;
    let playbackFrame = null;
    // 目前圖層的 node 中心與半徑，供 rider 經過 node 時的淡化判斷使用（以空間網格加速）。
    let currentLayerNodeCenters = [];
    let nodeGrid = null;
    // 全部站點的地理鄰近 mesh，建構一次後快取（站點層級用）。
    let globalStationMesh = null;
    // 著色節流時間戳（毫秒），避免 1739 個 node 每幀都重算顏色。
    let lastColorTimestamp = -1;
    // node counter-scale 的快取，避免單純平移時重複更新所有 node transform。
    let lastNodeScaleK = null;

    const DISTRICT_NODE_RADIUS = 256;
    const STATION_NODE_RADIUS = 30;
    // 站點文字（短 ID 與 %）只在放大到此倍率（相對 fit）以上才顯示；
    // fit/縮小時站點層級只看顏色點，不顯字。
    const STATION_TEXT_ZOOM = 1.8;

    // viewBox 跟著視窗實際像素（1:1），避免直長畫布在寬螢幕上以高度 fit、
    // 造成左右大量留白與整體被壓小。內容靠 transform 縮放平移填滿畫面。
    function syncViewBox() {{
      const width = shell.clientWidth || 1200;
      const height = shell.clientHeight || 700;
      svg.setAttribute("viewBox", `0 0 ${{width}} ${{height}}`);
    }}
    syncViewBox();

    function svgElement(name, attributes) {{
      const element = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
      return element;
    }}

    function applyTransform() {{
      viewport.setAttribute("transform", `translate(${{transform.x}} ${{transform.y}}) scale(${{transform.scale}})`);
      const scaleChanged = updateNodeScale();
      if (scaleChanged) {{ drawRiders(); drawTrucks(); }} // 縮放倍率變了才重畫 rider/卡車，維持固定螢幕大小（平移不觸發）。
      updateLabelVisibility();
    }}

    // node / 文字採「部分」反向縮放：放大時 node 以 √縮放 變大（看得清字），
    // 但間距是線性放大（更快），所以同時達到「放大可讀」與「拉開解擠」。
    // 指數 1 = 完全固定大小；0 = 跟著縮放等比放大；0.5 介於兩者之間。
    const NODE_SCALE_EXPONENT = 0.5;
    function nodeScaleFactor() {{
      return Math.pow(baseFitScale / transform.scale, NODE_SCALE_EXPONENT);
    }}

    function updateNodeScale() {{
      const k = nodeScaleFactor();
      if (k === lastNodeScaleK) return false;
      lastNodeScaleK = k;
      // node 與 集散站 都是持久 group，依 √縮放 反向縮放維持可讀大小。
      viewport.querySelectorAll("g[data-x]").forEach((group) => {{
        const x = group.getAttribute("data-x");
        const y = group.getAttribute("data-y");
        group.setAttribute("transform", `translate(${{x}} ${{y}}) scale(${{k}})`);
      }});
      return true;
    }}

    function getCanvasSize() {{
      // 回傳目前畫面（shell）的實際像素大小，作為 fit 與縮放的基準。
      return {{
        width: shell.clientWidth || 1200,
        height: shell.clientHeight || 700
      }};
    }}

    function currentLayerPoints() {{
      if (viewMode === "district") {{
        return Object.values(districtNodePos);
      }}
      // 站點層級雖然畫出全部站點，但 fit/重設只聚焦目前選定的行政區，
      // 維持與單區檢視相同的縮放比例（其餘站點可平移查看）。
      return Object.values(stationPositions)
        .filter((pos) => pos.district === selectedDistrict);
    }}

    function fitCurrentLayer() {{
      const points = currentLayerPoints();
      if (!points.length) return;
      const canvas = getCanvasSize();
      const xs = points.map((point) => point.x);
      const ys = points.map((point) => point.y);
      const minX = Math.min(...xs);
      const maxX = Math.max(...xs);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);

      // 依照目前圖層的實際座標範圍自動 fit，讓預設畫面能看見該圖層全部 node。
      const desiredNodeRoom = viewMode === "district" ? 900 : 520;
      const boundsWidth = Math.max(maxX - minX, desiredNodeRoom);
      const boundsHeight = Math.max(maxY - minY, desiredNodeRoom);
      // margin 改用畫面像素（viewBox 已是像素），讓內容填滿視窗。
      const margin = viewMode === "district" ? 70 : 50;
      const fittedScale = Math.min(
        (canvas.width - margin * 2) / boundsWidth,
        (canvas.height - margin * 2) / boundsHeight
      );
      baseFitScale = Math.max(0.08, Math.min(18, fittedScale));
      transform = {{
        x: canvas.width / 2 - baseFitScale * (minX + maxX) / 2,
        y: canvas.height / 2 - baseFitScale * (minY + maxY) / 2,
        scale: baseFitScale
      }};
      applyTransform();
    }}

    function clientPointToSvg(clientX, clientY) {{
      const point = svg.createSVGPoint();
      point.x = clientX;
      point.y = clientY;
      return point.matrixTransform(svg.getScreenCTM().inverse());
    }}

    function clampScale(scale) {{
      // 上下限相對於 fit 倍率，使縮放範圍不受 viewBox 像素尺度影響。
      const minScale = baseFitScale * 0.4;
      const maxScale = baseFitScale * 40;
      return Math.max(minScale, Math.min(maxScale, scale));
    }}

    function stockColor(ratio) {{
      const bounded = Math.max(0, Math.min(1, ratio));
      const lerp = (a, b, t) => Math.round(a + (b - a) * t);
      const low = [196, 47, 47];
      const mid = [127, 191, 123];
      const high = [106, 61, 154];
      const from = bounded <= 0.5 ? low : mid;
      const to = bounded <= 0.5 ? mid : high;
      const t = bounded <= 0.5 ? bounded / 0.5 : (bounded - 0.5) / 0.5;
      return `rgb(${{lerp(from[0], to[0], t)}}, ${{lerp(from[1], to[1], t)}}, ${{lerp(from[2], to[2], t)}})`;
    }}

    // 卡車載車比例顏色：高對比，空（亮黃）→ 半（橘）→ 滿（深紫藍），與站點色盤（紅-綠-紫）區隔。
    function truckColor(ratio) {{
      const t = Math.max(0, Math.min(1, ratio));
      const lerp = (a, b, u) => Math.round(a + (b - a) * u);
      const empty = [255, 214, 71];   // 亮黃（空）
      const mid = [233, 124, 30];     // 橘（半載）
      const full = [40, 50, 120];     // 深紫藍（滿載）
      const from = t <= 0.5 ? empty : mid;
      const to = t <= 0.5 ? mid : full;
      const u = t <= 0.5 ? t / 0.5 : (t - 0.5) / 0.5;
      return `rgb(${{lerp(from[0], to[0], u)}}, ${{lerp(from[1], to[1], u)}}, ${{lerp(from[2], to[2], u)}})`;
    }}

    // 集散站庫存比例顏色：空（淺琥珀）→ 滿（深琥珀/橘），與站點/卡車都不同。
    function depotColor(ratio) {{
      const t = Math.max(0, Math.min(1, ratio));
      const lerp = (a, b) => Math.round(a + (b - a) * t);
      const empty = [255, 243, 214];
      const full = [214, 138, 22];
      return `rgb(${{lerp(empty[0], full[0])}}, ${{lerp(empty[1], full[1])}}, ${{lerp(empty[2], full[2])}})`;
    }}

    // 集散站在某時刻有幾台卡車停泊（由各卡車軌跡推出）。
    function trucksAtDepot(district, time) {{
      let n = 0;
      truckLegsById.forEach((legs) => {{
        const s = truckStateAt(legs, time);
        // 母車（夜間）不計入集散場常駐 🚚（它抵達後即收工，不屬白天車隊）。
        if (s && s.atDepot && s.kind !== "mother" && s.depotId === district) n += 1;
      }});
      return n;
    }}

    // 「集散站在庫率」指標：某行政區此刻有多少比例的車隊車輛是停在集散站（未投入流通）。
    // = 集散站庫存 / (集散站庫存 + 該區站點現有車 + 該區卡車載量)。低 = 車多在外流通。
    function depotParkedRatio(district, time) {{
      const inv = depotInventoryAt(district, time);
      if (inv === null) return null;
      let stationBikes = 0;
      (stationsByDistrict.get(district) || []).forEach((sid) => {{
        stationBikes += stationBikesAt(sid, time);
      }});
      let truckLoad = 0;
      truckLegsById.forEach((legs) => {{
        if (legs.length && legs[0].district === district && legs[0].kind !== "mother") {{
          const s = truckStateAt(legs, time);
          if (s) truckLoad += s.load;
        }}
      }});
      const total = inv + stationBikes + truckLoad;
      return total > 0 ? inv / total : 0;
    }}

    // 用二分搜尋找出某站在指定時間的可借車數（階梯函式）。
    function stationBikesAt(stationId, time) {{
      const state = stationStates[stationId];
      if (!state) return 0;
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

    function stationRatioAt(stationId, time) {{
      const state = stationStates[stationId];
      if (!state || !state.capacity) return 0;
      return stationBikesAt(stationId, time) / state.capacity;
    }}

    function districtRatioAt(district, time) {{
      const stations = stationsByDistrict.get(district) || [];
      let bikes = 0;
      let capacity = 0;
      stations.forEach((stationId) => {{
        const state = stationStates[stationId];
        if (!state) return;
        bikes += stationBikesAt(stationId, time);
        capacity += state.capacity;
      }});
      return capacity ? bikes / capacity : 0;
    }}

    function clearLayers() {{
      boundaryLayer.replaceChildren();
      edgeLayer.replaceChildren();
      nodeLayer.replaceChildren();
      riderLayer.replaceChildren();
      depotLayer.replaceChildren();
      truckLayer.replaceChildren();
      lastNodeScaleK = null; // 重建 node 後需強制套用一次 counter-scale。
    }}

    // 畫指定行政區的界線（細灰線、不填色）。行政區層級畫全部，站點層級只畫當前區。
    function drawBoundaries(districtNames) {{
      districtNames.forEach((name) => {{
        const rings = districtBoundaries[name];
        if (!rings) return;
        rings.forEach((ring) => {{
          if (ring.length < 3) return;
          const commands = ring.map((point, index) => (
            `${{index === 0 ? "M" : "L"}} ${{point[0]}} ${{point[1]}}`
          )).join(" ") + " Z";
          boundaryLayer.appendChild(svgElement("path", {{
            class: "district-boundary",
            d: commands
          }}));
        }});
      }});
    }}

    function straightPath(a, b) {{
      return `M ${{a.x}} ${{a.y}} L ${{b.x}} ${{b.y}}`;
    }}

    function interpolate(a, b, t) {{
      return {{ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }};
    }}

    // 用空間網格把點分桶，供鄰近搜尋與 rider 淡化判斷加速（避免 O(n^2)）。
    function buildPointGrid(points, cell) {{
      const grid = new Map();
      points.forEach((point, index) => {{
        const key = `${{Math.floor(point.x / cell)}},${{Math.floor(point.y / cell)}}`;
        let bucket = grid.get(key);
        if (!bucket) {{ bucket = []; grid.set(key, bucket); }}
        bucket.push(index);
      }});
      return {{ cell, grid, points }};
    }}

    function neighborIndices(spatial, x, y, radiusCells) {{
      const cx = Math.floor(x / spatial.cell);
      const cy = Math.floor(y / spatial.cell);
      const result = [];
      for (let dx = -radiusCells; dx <= radiusCells; dx += 1) {{
        for (let dy = -radiusCells; dy <= radiusCells; dy += 1) {{
          const bucket = spatial.grid.get(`${{cx + dx}},${{cy + dy}}`);
          if (bucket) result.push(...bucket);
        }}
      }}
      return result;
    }}

    // 地理鄰近骨架：每個站點連到最近的 k 個站點（用網格找候選，去重）。
    // 這只是靜態底圖（像地圖街道），不是行駛路徑；rider 仍走起訖點直線。
    function buildGlobalMesh(k) {{
      if (globalStationMesh) return globalStationMesh;
      const points = Object.values(stationPositions).map((pos) => ({{ x: pos.x, y: pos.y }}));
      const cell = 500;
      const spatial = buildPointGrid(points, cell);
      const edges = [];
      const seen = new Set();
      for (let i = 0; i < points.length; i += 1) {{
        let candidates = neighborIndices(spatial, points[i].x, points[i].y, 1);
        if (candidates.length < k + 1) {{
          candidates = neighborIndices(spatial, points[i].x, points[i].y, 2);
        }}
        const ranked = candidates
          .filter((j) => j !== i)
          .map((j) => {{
            const dx = points[i].x - points[j].x;
            const dy = points[i].y - points[j].y;
            return [dx * dx + dy * dy, j];
          }})
          .sort((a, b) => a[0] - b[0]);
        for (let n = 0; n < Math.min(k, ranked.length); n += 1) {{
          const j = ranked[n][1];
          const key = i < j ? `${{i}}-${{j}}` : `${{j}}-${{i}}`;
          if (seen.has(key)) continue;
          seen.add(key);
          edges.push([points[i], points[j]]);
        }}
      }}
      globalStationMesh = edges;
      return edges;
    }}

    // rider 是否落在任一 node 上（用網格只檢查鄰近 node，O(1)）。
    function riderOverNodeGrid(point) {{
      if (!nodeGrid) return false;
      const k = nodeScaleFactor();
      const indices = neighborIndices(nodeGrid, point.x, point.y, 1);
      for (let i = 0; i < indices.length; i += 1) {{
        const node = nodeGrid.points[indices[i]];
        const dx = point.x - node.x;
        const dy = point.y - node.y;
        const effectiveR = node.r * k;
        if (dx * dx + dy * dy <= effectiveR * effectiveR) return true;
      }}
      return false;
    }}

    function drawDistrictLayer() {{
      clearLayers();
      drawBoundaries(Object.keys(districtBoundaries));
      const pairCounts = new Map();
      routes.filter((route) => !route.sameDistrict).forEach((route) => {{
        const key = `${{route.originDistrict}}->${{route.destinationDistrict}}`;
        pairCounts.set(key, (pairCounts.get(key) || 0) + 1);
      }});
      Array.from(pairCounts.entries()).forEach(([key, count]) => {{
        const [originDistrict, destinationDistrict] = key.split("->");
        const a = districtNodePos[originDistrict];
        const b = districtNodePos[destinationDistrict];
        if (!a || !b) return;
        edgeLayer.appendChild(svgElement("path", {{
          class: "district-edge",
          d: straightPath(a, b),
          "stroke-width": Math.max(1.2, Math.min(4, 1.2 + count / 60))
        }}));
      }});

      const k = nodeScaleFactor();
      Object.entries(districtNodePos).forEach(([district, pos]) => {{
        const ratio = districtRatioAt(district, currentTime);
        const group = svgElement("g", {{
          "data-kind": "district",
          "data-district": district,
          "data-x": pos.x,
          "data-y": pos.y,
          transform: `translate(${{pos.x}} ${{pos.y}}) scale(${{k}})`
        }});
        group.appendChild(svgElement("circle", {{
          class: "district-node",
          cx: 0,
          cy: 0,
          r: DISTRICT_NODE_RADIUS,
          fill: stockColor(ratio)
        }}));
        const label = svgElement("text", {{ class: "node-label", x: 0, y: -72, "font-size": 150 }});
        label.textContent = district;
        group.appendChild(label);
        const sub = svgElement("text", {{ class: "node-sub", x: 0, y: 78, "font-size": 150 }});
        sub.textContent = `${{Math.round(ratio * 100)}}%`;
        group.appendChild(sub);
        group.addEventListener("click", () => {{
          selectedDistrict = district;
          districtSelect.value = district;
          setViewMode("station");
        }});
        nodeLayer.appendChild(group);
      }});

      currentLayerNodeCenters = Object.values(districtNodePos)
        .map((pos) => ({{ x: pos.x, y: pos.y, r: DISTRICT_NODE_RADIUS }}));
      nodeGrid = buildPointGrid(currentLayerNodeCenters, 600);
      drawRiders();
      drawDepots();
      drawTrucks();
      fitCurrentLayer();
    }}

    function drawStationLayer() {{
      clearLayers();
      // 站點層級畫出全部行政區界線當地圖底圖（focus 哪一區由 fit 決定）。
      drawBoundaries(Object.keys(districtBoundaries));

      // 靜態底圖：全市站點的地理鄰近骨架（每站連到最近 3 站）。
      buildGlobalMesh(3).forEach(([a, b]) => {{
        edgeLayer.appendChild(svgElement("path", {{
          class: "mesh-edge",
          d: straightPath(a, b)
        }}));
      }});

      // 畫出全部 1739 個站點（不分區）；跨區 rider 因此可直接連到真實目的站，不再需要「區外」。
      const k = nodeScaleFactor();
      Object.entries(stationPositions).forEach(([stationId, pos]) => {{
        const ratio = stationRatioAt(stationId, currentTime);
        const group = svgElement("g", {{
          "data-kind": "station",
          "data-station": stationId,
          "data-x": pos.x,
          "data-y": pos.y,
          transform: `translate(${{pos.x}} ${{pos.y}}) scale(${{k}})`
        }});
        group.appendChild(svgElement("circle", {{
          class: "station-node",
          cx: 0,
          cy: 0,
          r: STATION_NODE_RADIUS,
          fill: stockColor(ratio)
        }}));
        const label = svgElement("text", {{ class: "node-label detailed-label", x: 0, y: -7, "font-size": 12 }});
        label.textContent = pos.short_id;
        group.appendChild(label);
        const sub = svgElement("text", {{ class: "node-sub detailed-label", x: 0, y: 10, "font-size": 9 }});
        sub.textContent = `${{Math.round(ratio * 100)}}%`;
        group.appendChild(sub);
        nodeLayer.appendChild(group);
      }});

      currentLayerNodeCenters = Object.values(stationPositions)
        .map((pos) => ({{ x: pos.x, y: pos.y, r: STATION_NODE_RADIUS }}));
      nodeGrid = buildPointGrid(currentLayerNodeCenters, 120);
      drawRiders();
      drawDepots();
      drawTrucks();
      fitCurrentLayer();
    }}

    // 依目前時間重新計算每個 node 的顏色與 %（不重建 DOM）。
    function updateNodeColors() {{
      nodeLayer.querySelectorAll("g[data-kind]").forEach((group) => {{
        const kind = group.getAttribute("data-kind");
        const ratio = kind === "district"
          ? districtRatioAt(group.getAttribute("data-district"), currentTime)
          : stationRatioAt(group.getAttribute("data-station"), currentTime);
        const circle = group.querySelector("circle");
        if (circle) circle.setAttribute("fill", stockColor(ratio));
        const sub = group.querySelector(".node-sub");
        if (sub) sub.textContent = `${{Math.round(ratio * 100)}}%`;
      }});
    }}

    function drawRiders() {{
      riderLayer.replaceChildren();
      const activeRoutes = routes.filter((route) => route.start <= currentTime && currentTime <= route.end);
      const riderScale = nodeScaleFactor(); // rider 大小跟隨 node 的部分反向縮放
      const riderRadius = (viewMode === "district" ? 17 : 11) * riderScale;
      let drawn = 0;
      activeRoutes.forEach((route) => {{
        if (drawn >= 600) return;
        const progress = Math.max(0, Math.min(1, (currentTime - route.start) / route.duration));

        // 同站來回（起=終）：站點層級時改成較小的點，繞著原站 node 轉直到歸還時間。
        if (viewMode === "station" && route.origin === route.destination) {{
          const center = stationPositions[route.origin];
          if (!center) return;
          const orbitRadius = STATION_NODE_RADIUS * riderScale * 1.5;
          // 角度隨進度推進（繞約 2 圈），不同 rider 給相位偏移避免重疊。
          const angle = route.start * 0.5 + progress * 2 * Math.PI * 2;
          riderLayer.appendChild(svgElement("circle", {{
            class: "rider",
            cx: center.x + Math.cos(angle) * orbitRadius,
            cy: center.y + Math.sin(angle) * orbitRadius,
            r: riderRadius * 0.6,
            fill: "#4d8f5f",
            opacity: 0.85
          }}));
          drawn += 1;
          return;
        }}

        let a;
        let b;
        if (viewMode === "district") {{
          a = districtNodePos[route.originDistrict];
          b = districtNodePos[route.destinationDistrict];
        }} else {{
          // 全部站點都已畫出，跨區 rider 直接連到真實目的站。
          a = stationPositions[route.origin];
          b = stationPositions[route.destination];
        }}
        if (!a || !b) return;
        const point = interpolate(a, b, progress);
        // rider 經過 node 時不消失，而是明顯降低透明度，代表只是路過。
        const overNode = riderOverNodeGrid(point);
        riderLayer.appendChild(svgElement("circle", {{
          class: "rider",
          cx: point.x,
          cy: point.y,
          r: riderRadius,
          fill: route.sameDistrict ? "#4d8f5f" : "#b66a3c",
          opacity: overNode ? 0.2 : 0.9
        }}));
        drawn += 1;
      }});
      activeCountLabel.textContent = activeRoutes.length;
    }}

    const DEPOT_SIZE = 30; // 集散站圖示半邊長（node 單位，會被反向縮放）。

    // 畫出所有集散站（倉庫造型方塊 + 屋頂；持久 group，內容由 updateDepots 隨時間更新）。
    function drawDepots() {{
      depotLayer.replaceChildren();
      if (!hasDispatch) return;
      const k = nodeScaleFactor();
      Object.entries(depotPos).forEach(([district, pos]) => {{
        const group = svgElement("g", {{
          "data-kind": "depot", "data-district": district,
          "data-x": pos.x, "data-y": pos.y,
          transform: `translate(${{pos.x}} ${{pos.y}}) scale(${{k}})`
        }});
        const s = DEPOT_SIZE;
        // 倉庫本體（方塊）
        group.appendChild(svgElement("rect", {{
          class: "depot-box", x: -s, y: -s * 0.55, width: s * 2, height: s * 1.3,
          rx: 3, fill: depotColor(0.5)
        }}));
        // 屋頂三角
        group.appendChild(svgElement("path", {{
          class: "depot-roof", d: `M ${{-s - 4}} ${{-s * 0.55}} L 0 ${{-s * 1.25}} L ${{s + 4}} ${{-s * 0.55}} Z`
        }}));
        // 標籤：行政區 + 卡車/腳踏車數
        const lab = svgElement("text", {{ class: "depot-label", x: 0, y: -s * 1.55, "font-size": 16 }});
        lab.textContent = "集散 " + district;
        group.appendChild(lab);
        const sub = svgElement("text", {{ class: "depot-sub", x: 0, y: 4, "font-size": 15 }});
        group.appendChild(sub);
        const pct = svgElement("text", {{ class: "depot-pct", x: 0, y: s * 0.95, "font-size": 12 }});
        group.appendChild(pct);
        // 母車駐留標示（夜間母車抵達後顯示在屋頂上方）
        const mom = svgElement("text", {{ class: "depot-mother", x: 0, y: -s * 2.05, "font-size": 16 }});
        group.appendChild(mom);
        depotLayer.appendChild(group);
      }});
      updateDepots();
    }}

    // 依目前時間更新集散站顏色（庫存比例）與標籤（🚚卡車數 / 🚲腳踏車數 / 庫存%）。
    function updateDepots() {{
      if (!hasDispatch) return;
      depotLayer.querySelectorAll("g[data-kind='depot']").forEach((group) => {{
        const district = group.getAttribute("data-district");
        const inv = depotInventoryAt(district, currentTime);
        const cap = depotCapacity[district] || 300;
        const ratio = inv === null ? 0 : inv / cap;
        const trucks = trucksAtDepot(district, currentTime);
        const box = group.querySelector(".depot-box");
        if (box) box.setAttribute("fill", depotColor(ratio));
        const sub = group.querySelector(".depot-sub");
        if (sub) sub.textContent = `🚚${{trucks}}  🚲${{inv === null ? "-" : inv}}`;
        const pct = group.querySelector(".depot-pct");
        if (pct) pct.textContent = `庫存 ${{Math.round(ratio * 100)}}%`;
        // 母車駐留標示：夜間有母車抵達此集散場時顯示「母×N（+載車數）」。
        const mom = group.querySelector(".depot-mother");
        if (mom) {{
          const m = mothersAtDepot(district, currentTime);
          mom.textContent = m.n > 0 ? `母×${{m.n}}（+${{m.bikes}}）` : "";
        }}
      }});
    }}

    // 畫出目前在路上/服務中的卡車（長方形，顏色=載車比例 + 內部載量比例條，朝行進方向旋轉）。
    // 停泊在集散站的卡車不畫（避免擋住集散站標籤；其數量已顯示在集散站的 🚚 上）。
    function drawTrucks() {{
      truckLayer.replaceChildren();
      if (!hasDispatch) return;
      const k = nodeScaleFactor();
      truckLegsById.forEach((legs) => {{
        const s = truckStateAt(legs, currentTime);
        if (!s || s.atDepot) return; // 停在集散站的不畫
        const isMother = s.kind === "mother";
        // 母車（夜間跨區、容量 100）畫得比白天 P7 小卡車（容量 30）大且醒目。
        // 行政區層級的 node 半徑很大（256），母車額外放大才不會被比下去。
        const mBoost = isMother ? (viewMode === "district" ? 5 : 1.6) : 1;
        const w = (isMother ? 52 : 30) * k * mBoost, h = (isMother ? 22 : 14) * k * mBoost;
        const pad = 2 * k * mBoost, cab = (isMother ? 8 : 5) * k * mBoost;
        const ratio = s.cap ? s.load / s.cap : 0;
        const group = svgElement("g", {{
          transform: `translate(${{s.pos.x}} ${{s.pos.y}}) rotate(${{s.angle}})`
        }});
        // 車身（母車深藍底、白天車淺底當載量條背景）
        group.appendChild(svgElement("rect", {{
          class: isMother ? "truck mother" : "truck",
          x: -w / 2, y: -h / 2, width: w, height: h, rx: 2 * k,
          fill: isMother ? "#16244a" : "#eef2f5"
        }}));
        // 內部載量比例條：從車尾(-x)往車頭增長，寬度 ∝ 載車比例。
        const innerW = (w - 2 * pad - cab);
        const barW = Math.max(0, innerW * ratio);
        if (barW > 0) {{
          group.appendChild(svgElement("rect", {{
            x: -w / 2 + pad, y: -h / 2 + pad, width: barW, height: h - 2 * pad,
            rx: 1 * k, fill: isMother ? "#ffce3a" : truckColor(ratio), stroke: "none"
          }}));
        }}
        // 車頭（朝行進方向）
        group.appendChild(svgElement("rect", {{
          class: "truck-cab", x: w / 2 - cab, y: -h / 2, width: cab, height: h, rx: 1 * k
        }}));
        // 母車加「母」字（反向旋轉維持正立）
        if (isMother) {{
          const lbl = svgElement("text", {{
            class: "truck-mother-label", x: 0, y: 0, "font-size": 13 * k * mBoost,
            transform: `rotate(${{-s.angle}})`
          }});
          lbl.textContent = "母";
          group.appendChild(lbl);
        }}
        truckLayer.appendChild(group);
      }});
    }}

    function updateLabelVisibility() {{
      // 站點文字只在放大到 STATION_TEXT_ZOOM 以上才顯示；fit/縮小時只看顏色點。
      // 行政區層級文字一律顯示（node 已放大、不易重疊）。
      const showStationText = viewMode === "station" && transform.scale >= baseFitScale * STATION_TEXT_ZOOM;
      document.querySelectorAll(".detailed-label").forEach((item) => item.classList.toggle("hidden", !showStationText));
      document.querySelectorAll(".simple-label").forEach((item) => item.classList.add("hidden"));
    }}

    function setViewMode(mode) {{
      viewMode = mode;
      districtViewButton.classList.toggle("primary", mode === "district");
      stationViewButton.classList.toggle("primary", mode === "station");
      if (mode === "district") drawDistrictLayer();
      else drawStationLayer();
      updateDispatchMetric();
    }}

    function zoomAt(factor, x, y) {{
      const beforeX = (x - transform.x) / transform.scale;
      const beforeY = (y - transform.y) / transform.scale;
      transform.scale = clampScale(transform.scale * factor);
      transform.x = x - beforeX * transform.scale;
      transform.y = y - beforeY * transform.scale;
      applyTransform();
    }}

    function setCurrentTime(value, recolor) {{
      currentTime = Math.max(0, Math.min(summary.simulationMinutes, value));
      timeSlider.value = currentTime.toFixed(1);
      timeLabel.textContent = `${{currentTime.toFixed(1)}} min`;
      // recolor 預設為 true；播放時為了效能會節流（1739 個 node 不每幀重算）。
      if (recolor !== false) {{ updateNodeColors(); updateDepots(); }}
      drawRiders();
      drawTrucks();
      updateDispatchMetric();
    }}

    function pausePlayback() {{
      isPlaying = false;
      lastFrameTime = null;
      playPauseButton.textContent = "播放";
      if (playbackFrame !== null) {{
        cancelAnimationFrame(playbackFrame);
        playbackFrame = null;
      }}
    }}

    function playbackStep(timestamp) {{
      if (!isPlaying) return;
      if (lastFrameTime === null) lastFrameTime = timestamp;
      const elapsedSeconds = (timestamp - lastFrameTime) / 1000;
      lastFrameTime = timestamp;
      // node 著色節流到約每 120ms 一次；rider 仍每幀更新維持流暢。
      const recolor = (timestamp - lastColorTimestamp) >= 120;
      if (recolor) lastColorTimestamp = timestamp;
      setCurrentTime(currentTime + elapsedSeconds * Number(speedSelect.value), recolor);
      if (currentTime >= summary.simulationMinutes) {{
        updateNodeColors();
        updateDepots();
        drawTrucks();
        updateDispatchMetric();
        pausePlayback();
        return;
      }}
      playbackFrame = requestAnimationFrame(playbackStep);
    }}

    function togglePlayback() {{
      if (isPlaying) {{
        pausePlayback();
        return;
      }}
      if (currentTime >= summary.simulationMinutes) {{
        setCurrentTime(0);
      }}
      isPlaying = true;
      playPauseButton.textContent = "暫停";
      playbackFrame = requestAnimationFrame(playbackStep);
    }}

    Object.keys(districtPositions).sort().forEach((district) => {{
      const option = document.createElement("option");
      option.value = district;
      option.textContent = district;
      districtSelect.appendChild(option);
    }});
    districtSelect.value = selectedDistrict;
    districtSelect.addEventListener("change", () => {{
      selectedDistrict = districtSelect.value;
      // 站點層級已畫全部站點，切換行政區只需重新聚焦（不重畫）。
      if (viewMode === "station") fitCurrentLayer();
      else setViewMode("station");
    }});
    districtViewButton.addEventListener("click", () => setViewMode("district"));
    stationViewButton.addEventListener("click", () => setViewMode("station"));
    playPauseButton.addEventListener("click", togglePlayback);
    document.getElementById("zoom-in").addEventListener("click", () => {{
      const canvas = getCanvasSize();
      zoomAt(1.35, canvas.width / 2, canvas.height / 2);
    }});
    document.getElementById("zoom-out").addEventListener("click", () => {{
      const canvas = getCanvasSize();
      zoomAt(0.74, canvas.width / 2, canvas.height / 2);
    }});
    document.getElementById("reset-view").addEventListener("click", fitCurrentLayer);
    timeSlider.addEventListener("input", () => {{
      setCurrentTime(Number(timeSlider.value));
    }});
    shell.addEventListener("mousedown", (event) => {{
      dragging = true;
      shell.classList.add("dragging");
      dragStart = clientPointToSvg(event.clientX, event.clientY);
      dragTransformStart = {{ x: transform.x, y: transform.y }};
    }});
    window.addEventListener("mousemove", (event) => {{
      if (!dragging) return;
      const currentPoint = clientPointToSvg(event.clientX, event.clientY);
      transform.x = dragTransformStart.x + currentPoint.x - dragStart.x;
      transform.y = dragTransformStart.y + currentPoint.y - dragStart.y;
      applyTransform();
    }});
    window.addEventListener("mouseup", () => {{
      dragging = false;
      shell.classList.remove("dragging");
    }});
    shell.addEventListener("wheel", (event) => {{
      event.preventDefault();
      const point = clientPointToSvg(event.clientX, event.clientY);
      const factor = event.deltaY < 0 ? 1.12 : 0.89;
      zoomAt(factor, point.x, point.y);
    }}, {{ passive: false }});
    window.addEventListener("resize", () => {{
      syncViewBox();
      fitCurrentLayer();
    }});

    setViewMode("district");
  </script>
</body>
</html>"""


def generate_visualization(
    report_dir: Path,
    visualization_input_dir: Path,
    max_routes: int,
) -> Path:
    summary = load_json(report_dir / "summary.json")
    station_positions = load_json(visualization_input_dir / "station_positions.json")
    district_positions = load_json(visualization_input_dir / "district_positions.json")
    canvas_metadata = load_json(visualization_input_dir / "visualization_canvas_metadata.json")
    station_states = build_station_state_history(report_dir, summary)
    routes = load_routes(report_dir, max_routes)
    boundaries_path = visualization_input_dir / "district_boundaries.json"
    district_boundaries = load_json(boundaries_path) if boundaries_path.exists() else {}
    dispatch_path = report_dir / "dispatch_trajectory.json"
    dispatch_data = load_json(dispatch_path) if dispatch_path.exists() else None
    html = render_html(
        report_dir,
        summary,
        routes,
        station_positions,
        district_positions,
        canvas_metadata,
        station_states,
        district_boundaries,
        dispatch_data,
    )
    output_path = report_dir / "real_map_visualization.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build real-system map visualization.")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="real_system report directory; defaults to latest",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=PROJECT_ROOT / "report",
    )
    parser.add_argument(
        "--visualization-input-dir",
        type=Path,
        default=PROJECT_ROOT / "data/processed/visualization_inputs",
    )
    parser.add_argument(
        "--max-routes",
        type=int,
        default=20000,
        help="載入的 rider 路線上限；超過時全天時間分層抽樣。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_dir = args.report_dir or latest_real_report(args.report_root)
    output_path = generate_visualization(
        report_dir,
        args.visualization_input_dir,
        args.max_routes,
    )
    print(f"Visualization written to: {output_path}")


if __name__ == "__main__":
    main()
