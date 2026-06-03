# 預期升級清單

本文件保存「想到但暫時不實作」的升級想法。等未來真的完成後，再移到對應的 phase 報告或新的實作報告。

## OD 與需求資料

- [x] 初步決定起點站 = 終點站 OD 先獨立抽出，不混入一般移動 transition matrix。
- [x] 建立週間 OD transition matrix。
- [x] 建立週末 OD transition matrix。
- [x] 建立 matrix build report，記錄各 profile 摘要與缺失站點。
- [x] **借不到車＝流失，為刻意設定**：λ 由真實資料推估，借不到而轉去他站的需求已內生在他站 λ，故起點站流失才不會重複計算（決策，見 architecture_narrative 第 3 節）。
- [x] **同站來回接回模擬**：route planner 以 self_station_ratio（週間 0.109/週末 0.225，自 OD metadata 讀）抽中同站來回；佔用時間用較長分布（mean≈45 分）；移除 false start（已被原始資料過濾、影響可忽略）。視覺化以小點繞站 node。驗證命中校準目標（見 architecture_narrative）。
- [ ] 建立 transition matrix row sum 自動測試（目前手動抽查列和≈1）。
- [x] **改善「有 λ 但無 OD origin row」的 175 個站**：fallback 由「同區均勻」改為「該行政區典型出站分布」（pooled 平均所有有 row 的站，含真實區外比例與熱門目的站）。驗證 2hr smoke 中 172 筆 fallback 全改用 pooled。仍為近似（見 architecture_narrative 第 8 節）。
- [ ] 設計 low-count OD smoothing，避免某些起點站只有極少目的地。
- [x] **travel time 改 probabilistic**：已實作 `sample_trip_minutes`：道路距離 = 直線 × circuity~N(1.3,0.15)，車速~N(14,3) km/hr，每趟抽一次，參數可調並記入 summary。截斷改為 ±3 SD 重抽（circuity 保留 1.0 物理硬下限，但用重抽避免在 1.0 堆尖峰）。診斷確認有效速度 14/1.3≈10.8 與官方 ~10 km/hr 相符、模擬均值約 5.5 分接近官方眾數 5–7 分（見 architecture_narrative 第 7 節）。
- [ ] 評估 OD 目的地是否過度集中鄰近站（模擬中位距離 0.64km 略短於官方典型 ~1km）；這是旅行時間殘差偏短的主因。
- [ ] 討論是否需要把 `臺大專區` 視為獨立行政區，或合併回鄰近行政區。
- [x] 轉換每站 hourly NSPP arrival rate。
- [x] 回報並評估 4 個出現在 rate 檔、但不存在於 `youbike_static_info.csv` 的站點 ID，判斷是否為退役站、資料版本差異、站點編碼異動或 static 檔缺漏。
- [x] 將每站 hourly NSPP arrival rate 接入大規模 simulation scenario。
- [x] 驗證 OD matrix、arrival rate、station registry 能被目前 `Station`、`DummyNode`、`Rider`、`demand_generator` 架構順利採納。
- [x] **建立 weekday/weekend arrival rate 切換機制**：以 `stations_arrival_rates_weekday.csv` 與 `stations_arrival_rates_weekend.csv` 各建一份 clean 輸出（`arrival_rates_weekday_clean` / `arrival_rates_weekend_clean`，均 1739 站、套用排除規則）。runner 的 `--arrival-dir` 預設改為依 `--profile` 自動選對應目錄，並把來源記入 `summary.json` 的 `arrival_rate_source`。已用 weekday/weekend 各跑 2hr smoke 驗證，結果明顯不同。
- [x] 新增 station-first exact route planner，讓真實 OD transition matrix 能接入核心 Rider 流程。
- [x] 建立 district alias table，處理 `臺大專區` 與 `臺大公館校區` 這類資料來源命名差異。
- [x] 建立 clean arrival rate 與 clean transition matrix，排除 static 缺失站點並套用 district alias。
- [x] **重新建立 clean weekday/weekend arrival rate**：已以新 `stations_arrival_rates_weekday.csv`、`stations_arrival_rates_weekend.csv` 重跑 `build_arrival_rate_inputs.py` 並套用 `real_system_cleaning_rules.json`，輸出至 `arrival_rates_weekday_clean` / `arrival_rates_weekend_clean`（各 1739 站、與 static 完全對齊）。

## 大規模模擬

- [ ] 從 `youbike_static_info.csv` 建立完整 station registry。
- [x] 建立站點短 ID lookup table，將真實長站點 ID 映射成視覺化用短代號，並保留反查表。
- [x] 建立真實系統 scenario loader，整合 station registry、arrival rate、OD transition matrix、travel time function。
- [x] **接上真實 station capacity**：`scripts/build_station_capacity_inputs.py` 從 `youbike_dynamic_2026-04-28.csv` 的 `Quantity` 取得每站容量（1739 站全覆蓋）。輸出 `data/processed/station_capacity/station_capacity.json`。容量 min=7, max=99, mean=28.29。runner 已逐站採用。
- [x] **接上真實 initial bikes**：以 `kpis_1.csv` 的 `avg_fill_ratio` 計算 `round(capacity × avg_fill_ratio)`，全系統 initial 平均 10.42 台。1739 站全有資料、無缺漏。
- [x] **建立模擬結果與真實 KPI 的比較機制**：每次跑模擬都會把模擬見車率/見位率與 `kpis_1.csv` 的 `bike_avail_rate`/`dock_avail_rate` 對照（見下方「每次模擬數據報告」）。
- [x] 執行 1739 站點、2 小時真實系統 smoke simulation（已改用真實 capacity/initial）。
- [ ] 執行 1600+ 站點、一週模擬的效能測試（**系統建好前先不要跑全時長**；之後全時長一律跑 24 小時，最終推廣到一週）。
- [ ] **一週模擬的資料量對策**：線上逐事件累加指標、降採樣佔用率時間序列（每 ~5 分鐘）、event log 改可選/壓縮、rider 播放抽樣（見 architecture_narrative 第 12 節）。
- [ ] 檢查 event log 尺寸是否需要壓縮或分批輸出。
- [~] **建立「站點優良時段占比」服務指標**：06:00–23:59 內車輛維持容量 20%–80% 的時間比例；系統用全站平均、極端站看分布左尾/低百分位。
  - [x] 真實 benchmark 就位：`data/benchmark/percentage_of_excellent/`（每站每日 + 每日系統平均 + 分布圖；mean 0.607、median 0.603、P5 0.246、P10 0.329）。
  - [x] 在模擬端（run_metrics）實作同定義指標，並輸出與 benchmark 的比對（系統日均、分布、P5/P10）。已用首次 24h weekday 驗證：無調度模擬 25.9% vs 真實 60.7%（差距 = 調度價值）。
  - [x] 加入**時間解析**指標（每小時健康站比例 / 平均填充率軌跡 + `hourly_trajectory.png`），量化 time-to-fail。結果：無調度下 weekday/weekend 都約 07:00 跌破 benchmark（早高峰崩潰）。
- [x] **執行 24 小時 weekday + weekend 模擬**（seed 20260602, label baseline），確認指標管線、time-to-fail 軌跡、視覺化皆可用。
- [x] **報告資料夾人性化命名**：`real_system_{label}_{profile}_{時長}_seed{seed}_{timestamp}`（`--label` 預設 baseline）。
- [x] **查核 shortage≈full**：非 bug（換 seed 差距 3→994，獨立隨機量；鬆散單車守恆耦合）。
- [x] 修正 Windows cp950 console 無法印 `≤` 的崩潰（stdout 改 utf-8）。
- [ ] **建立 time-to-fail 校準/評估框架**：無調度模擬與真實用上述指標比對，找出顯著 divergence 起點當校準基準；調度策略目標是把 time-to-fail 推到涵蓋整個時段（見 architecture_narrative 第 10 節）。
- [ ] 跑多個 seed 取平均 + 信賴區間（校準後再做）。
- [x] **建立每次模擬的數據結果報告**：`src/youbike_sim/run_metrics.py` 產生填充率最高/最低站點、缺車（<3 台）時長排名、最忙站點、見車/見位率對比真實 benchmark，以及文字敘述分析；跑完直接印到 console，並寫入 `report.md` 與 `summary.json` 的 `metrics`。

## 視覺化與報告

- [x] 產生真實站點行政區層級座標資料，行政區 node 位置使用該行政區所有站點經緯度重心。
- [x] 產生 station layer 地圖式視覺化輸入資料，使用站點真實經緯度投影到畫布座標。
- [x] 設計經緯度到畫布座標的初版比例控制，確保後續 node 大小可維持清楚。
- [x] 建立第一版大型可平移畫布，讓使用者能像地圖一樣拖曳查看完整系統。
- [x] 在行政區層級與站點層級都加入縮放功能。
- [x] 設計縮放層級規則：縮小到一定程度時，node 文字只顯示短站點 ID 與狀態顏色。
- [x] 讓真實地圖視覺化進入圖層時自動 fit 該圖層全部 node，並提高可放大上限。
- [x] 為真實地圖視覺化加入播放、暫停與時間速度控制。
- [x] 設計大型網路的基礎 edge routing，將直線改為曲線偏移。
- [ ] ~~設計精準 edge/node collision avoidance，保證線段不穿過 node 或 rider。~~ **改方向**：使用者決定放棄繞行，全部 edge 改回直線，改以圖層順序（node 蓋住 edge 與 rider）解決重疊（見 phase7）。
- [x] **(phase7-1) Edge 改直線**：已將 `curvedPath` 改為 `straightPath` 直線，rider 改線性插值。補充需求：rider 過 node 不消失改淡化（opacity 0.2）。
- [x] **(phase7-2) 修正 node 顏色/% 凍結問題**：已由 events.csv 重建每站時序佔用率（`build_station_state_history` + JS `stationBikesAt`），依 currentTime 即時更新顏色與 %。站點層級 t=120 已可見 20%～100% 變化。確認模擬本身正常，問題純在視覺化。
- [x] **(phase7-style) 採用 simple scenario 視覺風格**：深藍標題、卡片面板、色彩圖例。
- [x] **(phase7-3) 解決畫面比例**：根因為 viewBox 直長畫布在寬螢幕以高度 fit→左右留白＋整體偏小。已改 viewBox 跟隨視窗像素（1:1）填滿；node 改部分反向縮放（指數 0.5：放大可讀又解擠）；站點 fit 只顯色點、放大才顯字；行政區 node/字放大、fit 即可讀；rider 放大基準＋細外框；clampScale 改相對倍率。維持等比以利 Step C 疊真實邊界。
- [x] **(phase7-A2/A3) 站點層級 edge**：移除射出畫面的跨區線、改「區外」中繼節點；以地理鄰近 mesh 取代稀疏 OD 線、消除孤立 node。
- [x] **(phase7-4) 疊加台北市 12 行政區邊界**：來源 `Taipei_district_graph/`（TWD97 TM2 / EPSG:3826），已用 pyshp+pyproj 轉 WGS84 再套用站點相同投影（`scripts/build_district_boundaries.py` → `district_boundaries.json`）。新增最底層 boundary-layer，細灰線不填色，行政區層級畫全部 12 區、站點層級畫當前區，對齊已驗證。
- [ ] 評估 1600+ node 動態播放的效能瓶頸，必要時加入 viewport culling，只畫目前視窗內的 node/rider。
- [ ] 加入真實站點視覺化的圖層切換：行政區層級、站點層級、heatmap/狀態層。
- [ ] 為大型模擬建立 shortage/full heatmap。
- [x] 為 real-system map playback 增加 max-routes 載入上限，避免短期報告讓瀏覽器過慢。
- [x] **修正 rider 只出現在清晨的 bug**：原本取「最前面 N 筆」route，24h 169k 條只載到前 8000（=前 6 小時），導致 06:00 後顏色一直變卻沒有 rider。改為**全天時間分層抽樣**、上限提高到 20000；驗證 14:00 有 466、20:00 有 583 個 rider（原為 0）。
- [ ] 為 7 天大型動態播放增加更完整的抽樣或分段載入模式。
- [ ] 建立期末報告用的固定圖表輸出流程。

### Phase 7 視覺化修正（詳見 `phase7_visualization_fixes_plan.md`）
- 4 項問題已完成診斷，建議實作順序：phase7-1（直線+圖層）→ phase7-2（時序著色）→ phase7-3（固定螢幕大小）→ phase7-4（行政區邊界）。
- 關鍵發現：問題 2 不是模擬 bug，模擬輸出正常；是視覺化只取最終 snapshot 且行政區層級平均洗平。
- 關鍵發現：行政區邊界是 TWD97 TM2 公尺，站點是 WGS84 度，疊圖前必須投影轉換。

## 調度政策（下一階段；設計見 architecture_narrative 第 14、16 節）

提案 + 組員規則已比對（見 architecture_narrative §16）。**兩項拍板決策（2026-06）**：
- **決策① 分區規模 = 貼真實規模 + 每區多車**：用真實 12 行政區（每區 ~145 站），每區配多台調度車
  （車數待校準；先查真實調度車隊規模，查不到取可辯護值 + 敏感度）。不採組員「每區~20 站」理想化抽象。
- **決策② 主服務指標 = SC Ratio 與 優良時段占比 並列同等呈現**。
  SC Ratio = ServiceLevel / DispatchingCost；ServiceLevel = 1 − (缺車+滿站)/(借+還)；Cost = C_trip×次數 + C_mileage×里程。

組員規則（高度參考做區內調度）：站點分型 易多站(抽至30%)/易少站(補至70%)、LCL20%/UCL80%；
Policy 1 固定巡迴（2 班×1.5hr）、Policy 2 動態觸發（5 分掃描、Score=(Cur−Tgt)²/(Dist+0.5)）；
卡車 15km/h、handling=3+0.5×台數、集散場 300 台。評估：架構合理、實作中等偏易、Policy1 弱(benchmark)/Policy2 強(挑戰者)。
三待釐清（先用預設）：Policy1「(km)最大」語意、易多/易少分類規則、集散場破壞單車守恆。

現況評估（已討論）：無調度下約 07:00 崩潰、午後健康站 ~20%。時間點/方向正確；絕對嚴重度可信但帶
三項不確定（λ 量級、無隔夜整備、空站需求流失移除空間自我修正）。**評估改以「相對改善」為主**（對絕對量級穩健）。

調度 benchmark 設計（已討論，待實作）：
- 三錨點：無調度 floor（~20%）／真實 ceiling（~60.7%）／benchmark 調度器（中間、要超越的線）。
- benchmark 調度器 = 「貪婪門檻式再平衡」：T 台卡車、容量 Q、用 travel-time 移動，閒置時前往偏離
  健康帶 [20,80]% 最嚴重的站，拉回 ~50%；成本 = 卡車里程/時數、搬運量、T。
- 公平比較：同需求/seed/初始，比服務（優良占比 mean/P5/P10/time-to-fail）與成本；政策須 Pareto 支配 benchmark。
- 校準選項：調 T 使 benchmark 優良占比 ≈ 60.7%（代表「最笨方式達真實服務」），政策須更省或更好。

- [x] 查真實台北 YouBike 車隊規模：全市 26,156 台；調度卡車無公開總數（錨點：每車每日~400台）。預設每區 4 車（見 narrative §16.7）。
- [x] Dispatching Truck class（載量 Q、handling=3+0.5×台數、車速 50km/h 高斯）；集散場置**站點幾何中位數**、有限庫存。→ `src/youbike_sim/dispatch.py`
- [x] 站點現況分型（取代靜態 易多/易少）：低於 LCL 補至 70%、高於 UCL 抽至 30%。
- [x] 補車/抽車事件（改 bikes/docks、記入 event log：truck_replenish/withdraw/depart/at_depot…）。
- [x] 可插拔政策模組（仿 route_planner）：none（無調度）/ fixed（Policy 1 固定巡迴）。**修掉載量過濾反向 bug（零延遲 hang）。**
- [x] 成本帳（出車次數、總里程）→ **SC Ratio** 已輸出至 summary/console。
- [x] **Policy 1 首次 24h 對照無調度**（見 narrative §16.9）：優良占比 25.9%→33.1%、缺車 −17.5%、time-to-fail 7→8h。明確但部分改善。
- [x] **Policy 2 動態觸發**（5 分掃描、危急/≥3警戒觸發、全車出動、Score=(Cur−Tgt)²/(Dist+0.5) 同分隨機、全區健康即返場）。兩 policy 皆保留於 `POLICIES`。
- [x] **三方對照（24h weekday）**：優良占比 無0.259 / P1 0.331 / P2 **0.538**（真實0.607）；P2 服務壓倒性勝、P1 SC Ratio 較高 → 互不 Pareto 支配（見 narrative §17）。
- [ ] 設定真實 C_trip / C_mileage（目前=1 佔位）；SC Ratio 結論對此敏感。
- [ ] 考慮 Policy 2 觸發門檻/冷卻調參（大區下幾乎連續出車）。
- [ ] 三者並列比較輸出/報告：無調度 / Policy 1(benchmark) / Policy 2（**SC Ratio 與優良占比/time-to-fail 並列；政策間用 Pareto**）。
- [ ] SC Ratio cost=0 陷阱：無調度成本 0→SC 無限大；SC Ratio 僅用於有調度政策間，無調度當 floor 另列。確定 C_trip/C_mileage 數值。
- [ ] 校準車隊規模／敏感度掃描每區車數 {1,2,4,6,8}。
- [x] **視覺化調度**：卡車（長方形、顏色=載車比例、朝行進方向）+ 集散站（倉庫標記、顏色=庫存比例、🚚🚲標籤）+「集散站在庫率」指標。見 narrative §16.10。
- [ ] weekend Policy 1 24h。
- [ ] 討論 truck route 是否也採用 district-level approximation（每區內站數多，可能需要）。

---

## 2026-06 最新進度（取代上方部分舊待辦；完整脈絡見 architecture_narrative §16–§24、policy_rules.md）

### 已完成 ✅
- [x] **成本模型（含人力）**：`Cost = C_labor·工時 + C_km·里程 + C_trip·出車`，預設 NT$ 300/8/50；工時=值勤含待命。取代舊的佔位係數=1。
- [x] **閘門 + 人力休息**：行動量門檻（**改用比率** `偏離/容量 ≥ 0.15`，§24.1）、值勤窗 06–24、連續4hr休45min、返場冷卻10min。
- [x] **P3 hybrid_anticipatory（排程預置+反應10/90）、P4 hybrid_smartshift（排定班次+平方選站+提早收工）**；全部政策保留於 `POLICIES`。
- [x] **exp01 五方對照**（none/P1/P2/P3/P4，24h）；**exp02 部分時段待命**（SC Ratio +31%）；**exp03 車數敏感度掃描**（4政策×{2,4,6,8,10}）+ Pareto 圖。
- [x] **報告分類**：`--report-subdir`（exp01/02/03 各自歸檔）；`--duty-windows` 多段值勤窗。
- [x] **true standard 重新框定**：以真實 0.607 為評估錨點；發現**無一政策達標**（最佳 P3 n=10=0.584）。圖 `vs_true_standard.png`。
- [x] **報告/分析全面更新**：政策感知敘述、調度與成本區塊、vs true standard 判定；標題改 `Real System Report — {政策}`；29 份 report.md 重生。trajectory 標題 bug 修正。
- [x] **調度視覺化**：卡車（載量比例條+朝向）+ 集散站（庫存色+🚚🚲）+ 在庫率指標。

### 關鍵發現
- 人力（待命）成本主導（~80%）→ **排班模式比演算法精巧更關鍵**：P3≈P2、P4≈P1。
- 服務天花板：排程族 ~0.365、待命族 ~0.58，**蠻力加車到不了 0.607**（膝點 n≈6）。
- **集散場在區內調度下全歸零**（§24.2）→ 缺的是「跨區/隔夜」能力，不是車數。

### 待辦 / 下一階段
- [ ] **P7 參數敏感度 grid（已擱置、之後必做，§38/req5）**：基礎建設已就緒——
  scenario `--dispatch-config-override "key=val"`（覆寫任意 DispatchConfig 欄位）+ `--variant-label`，
  `plot_sc_pareto.py` 支援 variant_label。回來時只需定 grid（建議 OFAT @6車：目標水位70/30、健康帶80/20、
  掃描頻率5、距離常數0.5、demand-alpha、min-action-ratio 各繞預設掃）→ 跑 → 畫 P7 變體 SC/EC 效率圖。
- **06:00 快照接入**（決策＋鷹架已完成，見 narrative §32）：主實驗改「**06:00 起跑 + 快照當初始**」。
  - [x] 鷹架已實作＋冒煙測試：`simpy.Environment(initial_time)`、`--start-minute`(自動 360)、`--snapshot-csv`(容錯欄名)、集散場殘差自動、window_start 修次要指標、P6 跳過起點前預置。守恆精確、無 crash。
  - [ ] 快照到後：`--snapshot-csv <檔> --hours 18` 重跑所有實驗定案；若欄名不符微調 `load_station_snapshot`。
  - [ ] 快照另留作之後區間政策的「驗證目標」（產出 06:00 分布 vs 真實快照）。
- [ ] **區間（inter-district）調度**（概念已記於 policy_rules.md C 節，**本輪暫緩、之後討論**）：
  - 使用者標準：**區內效率應撐過一天**，夜間才做區間；故**不強制集散場非空**。
  - 簡單 benchmark＝隔夜把集散場補回目標（補車來源待定：外部注入 vs 隔夜抽回）；控制變數＝夜間 vs 日間時間分離。
  - 組員母車「場間搬車」版在真實規模下近乎無效（集散場全空、非不平衡）。
- [ ] weekend 各政策對照；最終報告固定圖表管線。
- [ ] 一週模擬資料量對策（線上指標、降採樣）。
- [ ] 多 seed 信賴區間。
