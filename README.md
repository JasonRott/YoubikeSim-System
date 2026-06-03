# YouBike 2.0 調度模擬系統（Discrete-Event Simulation）

台北市 YouBike 2.0 的 SimPy 離散事件模擬，比較各種調度政策以求「服務最大化、成本最小化（SC Ratio）」，
並延伸到多日連續模擬與夜間跨區母車調度。**最終工作點：P7 配對協調、∝需求 104 台、健康帶 0.40/0.60
（PoE 0.601、SC 1.111）。**

> 完整學術敘事、結論與圖表見 **`簡報材料/`**；逐步工程脈絡見 **`system_upgrade/architecture_narrative.md`**。

## 專案結構
```
src/youbike_sim/   核心 SimPy DES（Station/Depot/Rider/dispatch 政策）
scenarios/         真實系統情境主程式（real_system_scenario.py）
scripts/           前處理(build_*)、實驗(run_*/tune/pareto)、視覺化(visualize_*)、繪圖(plot_*)
data/
  raw/             原始資料（站點靜態/動態、KPI、到達率、OD geojson、車輛數）
  snapshots/       初始車輛快照（initial_bikes_{4am,6am}_{weekday,weekend}.csv）→ --snapshot-csv
  derived/         衍生小檔（晨峰淨流量）
  geo/             行政區 shapefile（Taipei_district_graph）
  processed/       前處理產物（到達率、轉移矩陣、視覺化輸入）
  benchmark/ config/
results/           精選成果（最終 Pareto、30 天 SC 交叉圖與分析）
docs/              Proposal（不入 git）
簡報材料/           組員簡報用：系統/政策/實驗/結論 + 圖表 + 數據彙整
system_upgrade/    工程脈絡與設計決策（architecture_narrative.md / policy_rules.md / REORG_PLAN.md）
report/            模擬輸出（65GB，不入 git；可由 scripts 重跑產生）
```
> 註：`data/raw/youbike_dynamic_2026-04-28.csv`（111MB）與 `report/`（65GB）超過 GitHub 限制，已 gitignore；
> 前者為可重建原始資料，後者關鍵成果已精選至 `results/`。

---

## （歷史）Phase 1 baseline skeleton
以下為最初 Phase 1 的說明，保留作為起點紀錄；當時只模擬使用者自然借還車、不含調度卡車。

## 目前新增內容

- `src/youbike_sim/baseline.py`：核心 SimPy DES 類別與流程。
- `src/youbike_sim/__init__.py`：整理 package 對外匯出的類別與函式。
- `examples/run_baseline_example.py`：最小可執行範例，示範如何組裝站點、Dummy Node、OD 機率與 NSPP demand generator。

## 核心物件

- `Station`：實體站點，使用 `simpy.Container` 管理 bikes 與 docks。
- `DummyNode`：行政區 hub，負責抽樣目的行政區與目的站點。
- `Rider`：使用者 process，負責租車、三段式移動、還車與滿站後再尋站。
- `BaselineModel`：保存全域網路資料，讓 Rider 可以查找所有站點與 Dummy Node。
- `demand_generator`：依照分段常數 NSPP lambda 產生 Rider。

## 為什麼需要 Dummy Node

如果直接建立所有站點到所有站點的 OD matrix，1000 個站點會形成約 100 萬個 OD pair。
本模型用行政區 Dummy Node 把路徑拆成：

1. 起點站點到起點行政區 Dummy Node。
2. 起點 Dummy Node 到目的行政區 Dummy Node。
3. 目的 Dummy Node 到目的實體站點。

這樣能把巨大 station-to-station OD 拆成較小的 district-level 與 district-internal transition。

## 滿站邏輯

Rider 到達目的站後若遇到滿站：

1. `Station.return_bike()` 記錄 `full_station` 事件。
2. Rider 抽樣一段願意等待時間，預設是平均 3 分鐘的 exponential。
3. 等待後重試原站。
4. 若仍滿站，前往附近有空柱的站點，直到成功還車。

目前「最近站點」邏輯是可替換的 placeholder；若沒有提供距離函式，會先找同行政區有空柱的站，再找全系統。

## 執行範例

```powershell
python examples/run_baseline_example.py
```

如果本機沒有 `python` 指令，請改用可用的 Python 執行器。專案需要 `simpy`。

## 簡單情境測試

兩行政區、六站點的測試情境放在 `scenarios/simple_scenario.py`。

```powershell
python scenarios/simple_scenario.py --days 7 --seed 20260524
```

若要測試空站與滿站行為，可以降低站點容量：

```powershell
python scenarios/simple_scenario.py --days 7 --seed 20260527 --station-capacity 6 --initial-bikes 3
```

每次執行都會在 `report/` 底下新增一個獨立資料夾，例如：

```text
report/simple_scenario_YYYYMMDD_HHMMSS_seed20260524/
```

資料夾內會包含：

- `summary.json`：機器可讀的測試摘要。
- `events.csv`：完整事件紀錄。
- `station_snapshots.csv`：模擬結束時各站狀態。
- `report.md`：人類可讀的測試回報。

產生視覺化 HTML：

```powershell
python scripts/visualize_simple_scenario.py
```

若未指定 `--report-dir`，視覺化腳本會自動讀取最新一次 simple scenario 報告，
並在該資料夾內輸出 `visualization.html`。

`visualization.html` 目前有兩個頁籤：

- `摘要報告`：檢查總到達人數、OD 比例、旅行時間與各站狀態。
- `動態播放`：依照 `events.csv` 內的 `route_planned` 事件重播 rider 騎乘過程。第一層顯示行政區 node 與跨行政區 rider；點擊行政區後，第二層顯示該行政區內的站點與區內 rider。

動態播放頁可以播放、暫停、重設、拖曳時間滑桿，並調整「每秒播放多少模擬分鐘」。

動態播放頁的補充規則：

- 行政區層級中，A 到 B 與 B 到 A 的 rider 會分別出現在連線兩側，避免方向重疊。
- 站點層級中，跨行政區進出的 rider 會連到 `區外` 節點，表示該 rider 正在離開或進入此行政區。
- `區外` 節點放在站點圖層中央，並以虛線連到該行政區內的站點。
- node 顏色代表目前可借車輛占總容量比例：接近 0% 與 100% 都是較強烈的警示色，接近 50% 是較溫和的健康色。
- 若 rider 遇到滿站並選擇等待，node 內會顯示 `Q:n`；行政區層級代表該行政區 queue 總數，站點層級代表該站 queue 人數。
- 站點層級的 `區外` dummy node 會在 node 內顯示「進入 n」，代表目前正在從其他行政區進入此行政區的 rider 數。
- 簡單情境中，滿站後尋找可還車站使用等距隨機最近站 selector；也就是所有候選站距離相同時，隨機選擇其中一站。

## OD Transition Matrix

真實 OD GeoJSON 轉換腳本：

```powershell
python scripts/build_transition_matrices.py --input-dir . --output-dir data\processed\transition_matrices
```

目前輸出包含週間與週末兩套矩陣：

- `*_station_exit_transition_by_district.json`：起點站選擇同區站點或區外 Dummy Node。
- `*_inter_district_transition.json`：區外 Dummy Node 選擇目的行政區。
- `*_inbound_station_transition_by_district.json`：進入目的行政區後選擇目的站點。
- `*_inbound_station_transition_by_od_district.json`：保留 origin district 與 destination district 的細分入站分布。
- `*_self_station_records.csv`：起終點同站交易，先抽出供後續獨立建模。

## Arrival Rate Inputs

組員提供的 `stations_arrival_rates_2hr.csv` 可用以下腳本轉成每站、每小時 lambda：

```powershell
python scripts/build_arrival_rate_inputs.py --input-csv stations_arrival_rates_2hr.csv --static-csv youbike_static_info.csv --output-dir data\processed\arrival_rates
```

主要輸出：

- `hourly_rent_lambda_by_station.json`：租借到達率，可直接給 `demand_generator` 使用。
- `hourly_return_lambda_by_station.json`：還車到達率，先保留作為校準與驗證資料。
- `hourly_arrival_rates_long.csv`：長表格式，方便人工檢查。
- `arrival_rate_build_report.md`：轉換與資料品質摘要。

Python 讀取範例：

```python
from youbike_sim import get_station_hourly_lambda, load_hourly_lambda_by_station

rates = load_hourly_lambda_by_station(
    "data/processed/arrival_rates/hourly_rent_lambda_by_station.json"
)
station_lambda = get_station_hourly_lambda(rates, "500101001")
```

## Real System Preparation

真實資料採納檢查：

```powershell
python scripts/validate_real_system_inputs.py --project-root . --output-dir data\processed\input_validation --profile weekday
```

此檢查會輸出：

- `rate_stations_missing_from_static.csv`：rate 有但 static 沒有的站點。
- `real_system_input_validation_report.md`：資料採納檢查報告。

weekday / weekend 各自的 clean arrival rate（真實模擬使用，runner 依 profile 自動選用）：

```powershell
python scripts/build_arrival_rate_inputs.py --input-csv stations_arrival_rates_weekday.csv --static-csv youbike_static_info.csv --output-dir data\processed\arrival_rates_weekday_clean --excluded-stations-json data\config\real_system_cleaning_rules.json

python scripts/build_arrival_rate_inputs.py --input-csv stations_arrival_rates_weekend.csv --static-csv youbike_static_info.csv --output-dir data\processed\arrival_rates_weekend_clean --excluded-stations-json data\config\real_system_cleaning_rules.json
```

舊版單一 2hr 輸入（已被 weekday/weekend 取代，保留作參考）：

```powershell
python scripts/build_arrival_rate_inputs.py --input-csv stations_arrival_rates_2hr.csv --static-csv youbike_static_info.csv --output-dir data\processed\arrival_rates_clean --excluded-stations-json data\config\real_system_cleaning_rules.json

python scripts/build_transition_matrices.py --input-dir . --output-dir data\processed\transition_matrices_clean --cleaning-rules-json data\config\real_system_cleaning_rules.json --valid-stations-csv youbike_static_info.csv

python scripts/validate_real_system_inputs.py --project-root . --output-dir data\processed\input_validation_clean --profile weekday --arrival-dir data\processed\arrival_rates_clean --transition-dir data\processed\transition_matrices_clean
```

真實座標視覺化輸入：

```powershell
python scripts/build_real_visualization_inputs.py --static-csv youbike_static_info.csv --output-dir data\processed\visualization_inputs
```

此步驟會建立：

- `station_short_id_lookup.csv/json`
- `station_positions.json`
- `district_positions.json`
- `visualization_canvas_metadata.json`

台北市行政區邊界（疊在地圖視覺化底層）：

```powershell
python scripts/build_district_boundaries.py
```

此步驟讀取 `Taipei_district_graph/`（TWD97 TM2 / EPSG:3826 Shapefile），
用 `pyproj` 轉成 WGS84 後套用與站點相同的投影，輸出 `district_boundaries.json`。
需要 `pyshp` 與 `pyproj`（見 `requirements.txt`）。

建立真實 capacity / initial bikes（取自當日 dynamic 與 kpis）：

```powershell
python scripts/build_station_capacity_inputs.py
```

輸出 `data/processed/station_capacity/station_capacity.json`：每站 `capacity`（dynamic 的 `Quantity`）、
`initial_bikes`（`round(capacity × avg_fill_ratio)`）、以及見車率/見位率 benchmark。

執行真實系統 baseline（預設會自動採用真實 capacity / initial bikes，且依 `--profile` 自動選用對應的 weekday / weekend arrival rate）：

```powershell
python scenarios/real_system_scenario.py --hours 2 --seed 20260529 --profile weekday
python scenarios/real_system_scenario.py --hours 2 --seed 20260529 --profile weekend
```

`--profile` 同時決定 OD transition matrix 與 arrival rate；未指定 `--arrival-dir` 時會自動讀
`data/processed/arrival_rates_{profile}_clean`，並把實際來源記入 `summary.json` 的 `arrival_rate_source`。

跑完會在 console 直接印出「模擬數據結果報告」（平均填充率、見車/見位率對比真實 benchmark、
填充率最高/最低站點、缺車 <3 台時長最長站點、最忙站點），同樣內容也寫入該次 `report.md`
與 `summary.json` 的 `metrics`。若 `--capacity-json` 指定的檔案不存在，會退回
`--station-capacity` / `--initial-bikes` 假設值。

備註：系統完全建構好之前，建議只跑短時長（如 `--hours 2`）smoke test，先不要跑全時長模擬。

產生真實系統地圖式 HTML：

```powershell
python scripts/visualize_real_system.py --report-dir report\real_system_YYYYMMDD_HHMMSS_weekday_seed20260528
```

`real_map_visualization.html` 支援行政區層級與站點層級切換、拖曳平移、滾輪/按鈕縮放、
自動 fit 當前圖層、播放/暫停，以及 0.5 到 60 分/秒的時間速度控制。

輸出檔名：

```text
real_map_visualization.html
```
