# Phase 5 真實系統 Runner 與地圖式視覺化報告

建立日期：2026-05-28  
狀態：已完成第一版真實系統 baseline runner 與地圖式 HTML 視覺化

## 1. 真實系統 Baseline Runner

新增：

```text
scenarios/real_system_scenario.py
```

使用資料：

```text
data/processed/arrival_rates_clean/
data/processed/transition_matrices_clean/
data/processed/visualization_inputs/station_positions.json
```

目前 runner 已接上：

- 1739 個 clean static stations。
- 每站 hourly rent lambda。
- clean station-first OD transition matrix。
- `StationExitRoutePlanner`。
- 真實座標資料，用於旅行時間近似與最近站搜尋。

目前尚未接上：

- 真實 station capacity。
- 真實 initial bikes。
- 實證 travel time distribution。

因此目前容量與初始車輛是參數化假設：

```text
station_capacity = 30
initial_bikes = 15
speed_kmph = 12.0
```

## 2. Smoke Simulation 結果

執行設定：

```text
profile = weekday
simulation_hours = 2
seed = 20260528
station_capacity = 30
initial_bikes = 15
```

輸出資料夾：

```text
report/real_system_20260528_082142_weekday_seed20260528/
```

主要結果：

```text
rider_arrival = 4264
route_planned = 4264
shortage = 0
full_station = 58
return_wait_started = 34
search_nearby_station = 24
route_planner_fallback = 216
```

`route_planner_fallback` 代表某些有 arrival rate 的站點在 OD matrix 中沒有 station-specific row，
因此 planner 退回同行政區均勻選站，讓模擬不中斷。

## 3. 地圖式 HTML 視覺化

新增：

```text
scripts/visualize_real_system.py
```

輸出：

```text
report/real_system_20260528_082142_weekday_seed20260528/real_map_visualization.html
```

目前支援：

- 大型 SVG 畫布。
- 真實站點經緯度投影座標。
- 行政區層級，使用行政區站點重心。
- 點擊行政區後查看該行政區站點層級。
- 站點短 ID，例如 `S0001`。
- 拖曳平移。
- 滾輪與按鈕縮放。
- 縮小時簡化站點文字。
- 依 final bike ratio 顯示 node 顏色。
- route edges 使用基礎曲線偏移。
- time slider 顯示 active riders。

目前限制：

- edge routing 只是基礎曲線偏移，尚未保證完全避開所有 node。
- 站點層級目前只顯示單一行政區，不是一次顯示全市所有站點。
- active riders 目前載入 route sample 上限，避免大型報告讓瀏覽器過慢。
- 未完成瀏覽器截圖驗證；本次 browser automation 環境缺少 browser client module。

## 4. 驗證

已完成：

- `scenarios/real_system_scenario.py` 通過 `py_compile`。
- `scripts/visualize_real_system.py` 通過 `py_compile`。
- 2 小時真實系統 smoke simulation 成功完成。
- `real_map_visualization.html` 成功產生。
- 靜態檢查確認 HTML 包含 pan/zoom、短 ID、真實 station positions、district positions、curved path、縮放文字切換。

## 5. 後續

下一步應優先處理：

1. 真實 station capacity 與 initial bikes。
2. 7 天完整模擬的效能與 event log 尺寸測試。
3. 地圖式 HTML 的瀏覽器實測與視覺修正。
4. 更精準的 edge/node collision avoidance。
