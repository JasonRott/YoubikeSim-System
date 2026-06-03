# Phase 4 真實系統模擬準備報告

建立日期：2026-05-27  
狀態：已完成真實資料採納檢查、exact route planner、真實座標視覺化輸入

## 1. Rate 有但 Static 沒有的站點

驗證腳本：

```text
scripts/validate_real_system_inputs.py
```

輸出：

```text
data/processed/input_validation/
  rate_stations_missing_from_static.csv
  real_system_input_validation.json
  real_system_input_validation_report.md
```

4 個出現在 `stations_arrival_rates_2hr.csv`，但不存在於 `youbike_static_info.csv` 的站點：

```text
500105088
500108127
500108186
500110108
```

評估結果：

- 這 4 個站點都有完整 12 個 2 小時 arrival rate period。
- 但缺少 static 位置、站名、行政區資料，因此不能建立 `Station`，也不能用真實經緯度視覺化。
- `500105088` 在週間 OD 中有少量 origin count，OD 站名為 `樟新街8巷`，行政區為 `文山區`。
- 其他 3 個站點未在目前 OD GeoJSON 中觀察到。
- 第一版真實模擬應先排除這 4 個站點；若之後能補齊 static 資料，再納入。

已建立清理規則：

```text
data/config/real_system_cleaning_rules.json
```

清理後 arrival rate 輸出：

```text
data/processed/arrival_rates_clean/
```

清理後結果：

```text
station_count_in_rate_file = 1739
station_count_in_static_file = 1739
stations_in_rate_not_in_static = []
stations_in_static_not_in_rate = []
```

## 2. 資料採納檢查

已確認：

- `youbike_static_info.csv` 的 1739 個站點都能建立 `Station`。
- static 中所有站點都有 hourly rent lambda。
- `hourly_rent_lambda_by_station.json` 可直接餵給 `demand_generator`。
- 使用 5 個站點做 120 分鐘 smoke test 已通過。

原始資料中發現的資料分類問題：

```text
transition matrix 中有：臺大專區
static station 中有：臺大公館校區
```

已檢查兩者是否為同一批站點：

```text
static 臺大公館校區站點數：60
週末 OD 臺大專區站點數：40，全部可對上 static 臺大公館校區，站名無不一致
週間 OD 臺大專區站點數：56，全部可對上 static 臺大公館校區，站名無不一致
```

因此第一版清理規則採用：

```text
臺大專區 -> 臺大公館校區
```

清理後 transition matrix 輸出：

```text
data/processed/transition_matrices_clean/
```

清理後驗證：

```text
weekday transition 有但 static 沒有的 station id 數 = 0
weekend transition 有但 static 沒有的 station id 數 = 0
weekday transition 有但 static 沒有的行政區數 = 0
weekend transition 有但 static 沒有的行政區數 = 0
```

## 3. Exact Route Planner

已新增：

```text
src/youbike_sim/routing.py
```

並更新：

```text
src/youbike_sim/baseline.py
src/youbike_sim/__init__.py
```

核心變更：

- `BaselineModel` 現在可接受自訂 `route_planner`。
- 新增 `StationExitRoutePlanner`，支援 station-first routing：

```text
origin station
-> same-district destination station
or
-> __OUT_OF_DISTRICT__
   -> destination district
   -> destination station
```

這讓目前真實 OD transition matrix 可以被核心 Rider 流程採納。

## 4. 真實座標視覺化輸入

已新增：

```text
scripts/build_real_visualization_inputs.py
```

輸出：

```text
data/processed/visualization_inputs/
  station_short_id_lookup.csv
  station_short_id_lookup.json
  short_id_by_station_id.json
  station_id_by_short_id.json
  station_positions.json
  district_positions.json
  visualization_canvas_metadata.json
  real_visualization_input_build_report.md
```

目前座標設定：

```text
station count = 1739
district count = 13
canvas width = 8000 px
canvas height = 9202 px
padding = 160 px
projection = local_equirectangular
```

短 ID 命名規則：

```text
500101001 -> S0001
500101002 -> S0002
...
```

行政區 node 位置：

- 使用該行政區內所有站點投影後座標的平均值。
- 此資料已輸出到 `district_positions.json`。

## 5. 尚未完成

- 尚未建立完整大規模 simulation runner。
- 尚未接入真實 station capacity 與 initial bikes。
- 尚未建立地圖式 pan/zoom HTML 視覺化。
- 尚未實作 edge routing 避免線段穿過 node。
- 已建立第一版 district alias table；未來若新增資料來源仍需重新檢查命名差異。
