# Phase 1 Baseline 模型現況

建立日期：2026-05-27  
狀態：已完成小型 baseline skeleton 與簡單情境驗證  
目前範圍：只模擬使用者自然借還車，不包含調度卡車

## 1. 目前已完成內容

目前專案已具備以下核心元件：

1. `Station`
   - 使用 `simpy.Container` 管理 bikes 與 docks。
   - 記錄 shortage、full-station、rental、return 次數。

2. `DummyNode`
   - 作為行政區 hub。
   - 支援由起點行政區抽目的行政區。
   - 支援由目的行政區內部 station transition matrix 抽目的站點。

3. `Rider`
   - 模擬 rider arrival、租車、三段式路由、還車。
   - 若起點站無車，記錄 shortage 並 balk。
   - 若目的站滿站，先等待，再尋找附近可停車站。

4. `demand_generator`
   - 使用 hourly lambda 產生分段常數 NSPP arrival。
   - lambda 單位為人/hr，模型內部換算成每分鐘到達率。

5. `BaselineModel`
   - 管理所有 station、dummy node、event log、路徑規劃與旅行時間函式。

## 2. 已完成的簡單情境測試

目前已建立 `scenarios/simple_scenario.py`：

- 兩個行政區：A、B。
- 每區三個站點：1、2、3。
- 每站 arrival rate = 3 人/hr。
- 同行政區目的地比例約 2/3。
- 跨行政區目的地比例約 1/3。
- 同行政區總旅行時間 10 分鐘。
- 跨行政區總旅行時間 24 分鐘。
- 每次測試會輸出到獨立的 `report/simple_scenario_*` 資料夾。

已建立 `scripts/visualize_simple_scenario.py`：

- 產生單一 `visualization.html`。
- 第一頁為摘要報告。
- 第二頁為動態播放，採用雙層互動式視覺化。
- 第一層顯示行政區 node 與跨行政區 rider。
- 點擊行政區後進入第二層，顯示該行政區內的站點與區內 rider，並可返回行政區層級。
- 行政區層級中，雙向跨區 rider 會分布在連線兩側，避免 A 到 B 與 B 到 A 重疊。
- 站點層級中，跨行政區進出事件會顯示為站點與 `區外` 節點之間的移動。
- `區外` 節點放在站點圖層中央，並以虛線連接站點，避免被誤認為實體 YouBike 站點。
- node 顏色會依照該時間點可借車輛占總容量比例變化；接近 0% 與 100% 都是警示狀態，中間比例較健康。
- 滿站等待中的 rider 會顯示為站點旁橘色 queue 點；超過 5 人時改以 `Q:n` 顯示，避免畫面過亂。

## 1.1 滿站後最近站選擇

目前核心模型已新增距離型最近站 selector：

```text
make_distance_based_station_selector(distance_func, rng)
```

規則：

1. 從目前所有有空柱的候選站中，計算與滿站站點的距離。
2. 選擇距離最短者。
3. 若多個站距離相同，使用 rng 隨機選擇，避免固定偏向 station id 較小的站。

簡單情境中，所有站點距離設定為相同，因此滿站後會在所有可還車候選站中隨機選擇。

## 2.1 低容量壓力測試

2026-05-27 新增低容量測試，用來確認 shortage、full-station、queue 與 node 顏色是否會正常反映。

執行設定：

```text
days = 7
seed = 20260527
station_capacity = 6
initial_bikes = 3
```

最新輸出資料夾：

```text
report/simple_scenario_20260527_212521_seed20260527/
```

測試結果：

- 預期總到達人數：約 3024.0。
- 實際總到達人數：3003。
- 成功規劃路線數：2364。
- shortage 事件數：639。
- full-station 事件數：315。
- return_wait_started 事件數：166。
- search_nearby_station 事件數：149。
- rider_finished_after_wait 事件數：17。

這組結果確認低容量情境下確實會出現無車、滿站與等待 queue，適合檢查節點顏色與 queue 是否隨時間變化。

## 2.2 低容量測試：初始車輛 4 台

2026-05-27 依照新需求重跑簡單情境，目標是讓滿站與等待 queue 現象更容易觀察。

測試設定：
```text
days = 7
seed = 20260527
station_capacity = 6
initial_bikes = 4
```

輸出資料夾：
```text
report/simple_scenario_20260527_213502_seed20260527/
```

主要結果：
- 預期總到達人數：3024.0。
- 實際總到達人數：2994。
- 成功規劃路線數：2666。
- shortage 事件數：328。
- full-station 事件數：813。
- return_wait_started 事件數：428。
- search_nearby_station 事件數：385。
- rider_finished_after_wait 事件數：42。

這組設定比初始 3 台車更容易產生滿站，因此更適合用來檢查「到達滿站後進入 queue、等待期間有空位則離開 queue、等待太久則改找附近站點」的視覺化流程。

## 2.3 Queue 視覺化顯示方式修正

2026-05-27 針對站點層級圖的 queue 顯示做小幅調整：

- 移除站點外側的 queue 小框與等待點，避免和路線、路上的 rider 重疊。
- node 內原本顯示 active 的位置改成顯示 `Q:n`，代表目前該站點正在等待還車的 rider 人數。
- 行政區層級 node 的 `Q:n` 代表該行政區內所有站點 queue 人數加總。
- 為了確保文字留在 node 內，queue 數字超過 99 時會以 `Q:99+` 顯示。
- 站點 node 第三行由 `33% bikes` 簡化為 `33%`，並調整三行文字位置，避免文字超出 node。
- 區外 dummy node 下方新增 `進入 n`，代表目前正在從其他行政區進入此行政區的 rider 數量。
- 行政區層級 node 半徑調整為與站點層級一致，避免兩個圖層比例落差過大。
- `Q:n` 改成黑色粗體並加白色描邊，提高在不同 node 顏色上的可讀性。
- `區外` dummy node 的 `進入 n` 移到 node 內部，避免外部文字造成視覺比例不一致。

## 3. 目前設計限制

1. 尚未讀入完整 1600+ 站點作為正式模擬網路。
2. 尚未把真實 OD GeoJSON 轉成 transition matrix。
3. 尚未接上每站真實 NSPP arrival rate。
4. 尚未接上真實站點容量與初始車輛數。
5. 尚未實作 dispatching truck。
6. 大規模一週模擬尚未做效能測試。

## 4. 下一個合理階段

下一步應先建立 OD transition matrix 的轉換規格，而不是直接寫轉檔程式。

原因：

- 週間與週末 OD 資料結構相同，但交易量與同站借還比例差異明顯。
- 起點站等於終點站的 OD 比例不低，需要先決定保留、排除或另行建模。
- 我們要避免 1600+ 站點形成巨大 station-to-station OD matrix，因此需要維持 Dummy Node 架構。
