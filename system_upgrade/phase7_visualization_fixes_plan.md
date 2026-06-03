# Phase 7 視覺化問題診斷與修正計畫

建立日期：2026-05-29
狀態：診斷完成。Step A（問題 1+2＋兩項補充＋風格）已完成並驗證；問題 3、4 待做。

主要來源檔：`scripts/visualize_real_system.py`
報告輸出：`report/<run>/real_map_visualization.html`

## 進度追蹤

| Step | 內容 | 狀態 |
|---|---|---|
| A | 問題1 edge 直線 + 問題2 時序著色 + rider 過 node 淡化 + simple scenario 風格 | ✅ 已完成並於瀏覽器驗證 |
| A2 | 站點層級 edge 修正：移除射向畫面外的跨區線，改用「區外」中繼節點 | ✅ 已完成並於瀏覽器驗證 |
| A3 | 站點層級孤立 node：先移除 edge 上限＋虛線補救，最終改用地理鄰近 mesh | ✅ 已完成並於瀏覽器驗證；0 孤立站 |
| B | 問題3 畫面比例：node/文字/rider/線寬固定螢幕大小 | ✅ 已完成並於瀏覽器驗證 |
| C | 問題4 台北市行政區邊界疊圖 | ✅ 已完成並於瀏覽器驗證 |

### Step A2：站點層級 edge 修正（使用者回報）
使用者檢查 Step A 後回報：行政區層級 edge 正常，但站點層級有很多看不懂的線。
- 根因：原本畫「任一端在本區」的路線，跨行政區路線的另一端站點在別區（畫面外真實座標），導致線射向看不見的遠方。
- 修法（移植 simple scenario 的「區外」gateway 設計）：
  - 站點層級只畫「兩端都在本區」的區內路線（實線），cap 改 220 條。
  - 新增「區外」中繼節點，放在本區站群上方並納入 fit。
  - 跨行政區 rider 改連到「區外」：出區 = 站點→區外；入區 = 區外→站點。
  - 「區外」節點顯示當下「進入 n」inbound rider 數（每幀更新）。
  - `updateNodeColors` 跳過 gateway 群組，避免被染色或覆蓋文字。
- 瀏覽器驗證（中山區 t=95.9）：所有 edge 端點落在站群範圍內（0 條射出）、rider 0 條飛出範圍、區外顯示「進入 12」、站點 % 散布 27%～100%。

### Step A3：站點層級孤立 node（使用者回報）
使用者回報站點層級「明顯有些站點完全沒有線」。資料盤點（中山區 200 站）：
- 區內 OD 路線 414 條、distinct 區內 pair 314；有區內移動的站點 157/200。
- 完全沒有任何移動（含跨區）的站點 32；只有跨區、無區內移動的 11。
- 原 220 條 edge 上限只覆蓋 136 站 → 上限砍太多。
修法：
- 移除 220 上限（safety cap 2000），畫出全部觀察到的區內 pair（中山從 220→314 條，覆蓋 157 站）。
- 對「無區內路線、但有跨區進出」的站點，補一條虛線連到區外（中山 11 條），避免活躍站看起來孤立。
- 底部說明加註：完全沒有連線的站點代表此時段內沒有任何借還移動。
驗證（中山區 t=0）：實線 314、虛線 11、孤立 32（= 真實零活動站，誠實保留）。
備註：剩餘 32 孤立站是真實資料稀疏（2 小時短窗）。

#### A3 最終決議：改用地理鄰近 mesh
使用者選擇「加地理鄰近 mesh」，並提出 rider 是否會被迫沿線段行駛而失真的疑慮。
- 設計澄清（重要）：mesh 是**靜態地理底圖**（每站連同區最近 3 站），不是行駛路徑；**rider 仍維持起訖點直線移動**（跨區則起點↔區外），不沿 mesh 跳節點，因此沒有失真。mesh 與 rider 是分離的兩層。
- 實作：新增 JS `buildProximityMesh(stationEntries, k=3)`（同區 k-NN、去重），以 `.mesh-edge` 淡灰細線畫在 edgeLayer。站點層級**移除**原本的觀察 OD 實線與虛線補救，流量改完全由 rider 呈現。
- 結果（中山區）：mesh 366 條、孤立站 0、mesh 端點 0 條超出站群；district 層級不受影響（45 邊、riders 正常）；station 層級 riders 仍正確連到區外（進入 12）。
- 未決：若日後想同時看「區內 OD 流量」靜態強弱，可再把觀察 OD 線以不同樣式疊回 mesh 之上（目前先保持乾淨，未做）。
- 預期：拉長模擬時間後零活動站會大幅減少；mesh 仍可作為穩定的地圖骨架。

### 使用者補充需求（已納入）
1. rider 經過其他 node 時不要完全消失（避免誤判為進入該 node），改為明顯降低透明度 → Step A 已實作（過 node 時 opacity 0.2，否則 0.9）。
2. 偏好 simple scenario 視覺化整體風格 → Step A 已採用（深藍 #233043 標題、卡片面板、色彩圖例、stockColor 一致）。

---

## 問題總覽（使用者回報 4 項）

| 編號 | 問題 | 類型 | 目前狀態 |
|---|---|---|---|
| 1 | edge 改成直線，並讓 node 蓋住 edge 與行駛中 rider | 視覺化 | 待實作 |
| 2 | node 顏色與 % 完全不變 | 視覺化（非模擬 bug） | 已診斷，待實作 |
| 3 | 畫面比例：node 不重疊但也不過小 | 視覺化 | 已診斷，待實作 |
| 4 | 疊加台北市行政區邊界 | 視覺化 + 資料前處理 | 已確認投影，待實作 |

---

## 問題 1：Edge 畫法與圖層覆蓋順序

### 現況
- Edge 使用二次貝茲曲線 `curvedPath()`（含法向偏移 offset），rider 沿曲線用 `pointOnCurve()` 移動。
- SVG 圖層順序：`edge-layer` → `node-layer` → `rider-layer`（rider 在最上層）。
- 因此 node 已會蓋住 edge，但 **rider 反而蓋在 node 上**。

### 使用者期望
- 全部 edge 改為**直線**（一般 graph 畫法），不再繞行、不再曲線偏移。
- z 軸由下到上應為：**edge（最底）→ rider（中）→ node（最上）**。
- 線段與行駛中車輛（小點）若與 node 重疊，都被 node 蓋住。

### 計畫
- `curvedPath()` 改為直線 `M ax ay L bx by`，移除 offset 參數依賴。
- `pointOnCurve()` 改為線性插值 `a + (b-a)*t`。
- 將 SVG 圖層順序改為 `edge-layer` → `rider-layer` → `node-layer`（把 rider-layer 移到 node-layer 之前）。
- 移除行政區層級 edge 的 `(index % 5) * 35` 與站點層級 `(index % 7) * 9` 等偏移量。

---

## 問題 2：node 顏色與 % 不變（重點：模擬本身正常）

### 模擬驗證結果（已確認模擬正常運作）
針對 `report/real_system_20260528_082142_weekday_seed20260528` 的最終 snapshot：

```text
available_bikes: min=3, max=30, mean=14.90
fill ratio: min=0.100, max=1.000, mean=0.497, stdev=0.102
偏離初始值 15 的站點：1283 / 1739
```

→ 模擬確實有改變各站狀態，狀態分布合理。**問題出在視覺化，不是模擬。**

### 視覺化根因（兩層）
1. **只用最終 snapshot 著色**：`build_station_states()` 只讀 `summary["station_snapshots"]`（模擬結束狀態）。`stationStates` 是固定的最終值，node 顏色在繪製時算一次後就不再更新。時間滑桿移動時 `setCurrentTime()` 只呼叫 `drawRiders()`，完全不重算 node 顏色 → 顏色凍結。
2. **行政區層級被平均洗平**：`districtRatio()` 把整個行政區數百站加總平均，個別站的高低互相抵消，使每區都接近 50%（48–51%），看起來像沒變化。

### 計畫
- 利用 `events.csv` 重建**每站佔用率時間序列**。事件已含所需欄位：
  - `rental` 事件：`station_id`, `time`, `bikes_after`
  - `return` 事件：`station_id`, `time`, `bikes_after`
  - 以 (time, bikes_after) 建立每站階梯函式，查詢任意 `currentTime` 的車輛數。
- node 顏色改為依 `currentTime` 即時計算（站點層級用各站佔用率；行政區層級用該時刻所有站加總佔用率）。
- 每次 `setCurrentTime()` 一併更新 node 顏色與 % 文字，不再只更新 rider。
- 待評估：時間序列資料量（rental+return ≈ 8353 筆）對瀏覽器負擔，必要時做時間分桶（例如每分鐘抽樣）。

### Step A 實作結果（已完成）
- `build_station_states()` 改為 `build_station_state_history()`，移植 simple scenario 的反向重建：以最終 snapshot 為基準，反向套用 rental(+1)/return(-1) 得到 initialBikes，並保留每站 (time, bikes) 階梯序列。
- JS 新增 `stationBikesAt()`（二分搜尋）、`stationRatioAt()`、`districtRatioAt()`（皆 time-aware）。
- `setCurrentTime()` 每次呼叫 `updateNodeColors()` 重算 node 顏色與 %，再 `drawRiders()`。
- edge 改 `straightPath()` 直線；rider 改 `interpolate()` 線性；`curvedPath`/`pointOnCurve` 已移除。
- 瀏覽器驗證（localhost 8765 + preview eval）：
  - edge d 屬性符合 `M x y L x y` 直線。
  - 行政區層級 t=0→t=120 顏色/%會變（但因整區平均，幅度小：50%→49%）。
  - 站點層級（大安區 210 站）t=0 全部 50%、t=120 散布 20%～100%，動態變化明顯。
  - rider 過 node opacity=0.2，否則 0.9。
- 截圖工具在本環境對超大 SVG（8000×9202）會逾時，無法自動截圖；功能已用 DOM/eval 驗證，視覺外觀待使用者確認。

---

### Step B 實作結果（已完成）
- 做法：每個 node（行政區/站點/區外）改包進 `g[transform="translate(x,y) scale(baseFitScale/scale)"]`，內容置於原點。位置隨視窗縮放拉開，但 scale 反向抵銷讓 node 本身維持固定螢幕大小。
- `applyTransform()` 在縮放倍率改變時更新所有 node 群組的 counter-scale（`updateNodeScale()`，平移時以 `lastNodeScaleK` 快取跳過），並重畫一次 rider 使其同步固定大小。
- rider 半徑 = 基準 × `nodeScaleFactor()`；edge/rider/node 外框加 `vector-effect: non-scaling-stroke`，線寬固定為螢幕像素。
- 預設 fit 畫面與之前相同（fit 時 k=1）；差別在「放大只拉開站距、node 不再一起放大」，讓縮放真正能解擠。
- 瀏覽器驗證：行政區與站點 node 直徑放大 3–4 倍後不變、rider 不變、mesh 線寬固定 1px、站距隨放大成長（112px→335px）。

#### Step B 修正（使用者回報）
使用者回報「完全固定大小」反而導致放大也看不清 node 上的字，且 rider 點被白色外框吃掉。
- 改為「部分」反向縮放：`nodeScaleFactor = (baseFitScale/scale)^0.5`（指數 NODE_SCALE_EXPONENT=0.5）。
  - 效果：放大時 node 以 √縮放 變大（可讀），間距是線性放大（更快），同時達到「放大可讀」與「拉開解擠」。fit 時指數底為 1 → 預設畫面不變。
  - 驗證（中山區，放大 4×）：node 直徑 12→23px（×2）、站距 ×4、重疊比 0.108→0.054（下降＝解擠）。
- rider 清晰度：基準半徑 8/14 → 11/17，外框 `stroke-width` 2 → 0.8、改半透明白，避免小點被外框佔滿。

#### Step B 再微調（使用者回報：兩層級需求不同）
- 站點層級：fit/overview 只看顏色 → 文字（短 ID＋%）改為僅在放大 ≥ `STATION_TEXT_ZOOM`(1.8×) 時顯示，fit 時隱藏（乾淨色點）；移除中間的 simple-label（一律隱藏）。
- 行政區層級：node 不易重疊 → 放大整體尺寸。`DISTRICT_NODE_RADIUS` 86 → 175、label font 26→40、sub 18→30，fit 即顯示文字。
- 驗證：行政區 fit node 直徑明顯放大、13 區無重疊（最近一對中心距 40px、node 23px、留 17px gap，preview 尺度）；站點層級文字 fit 隱藏、放大 2.5× 後顯示。

#### Step B 重大修正（依使用者錄影）：viewBox 比例
使用者錄影回報「整體比例怪、字級不直覺、觀察困難」。抽影格分析發現**根因是 SVG 畫布比例**：
- 原本 `viewBox = 0 0 8000 9202`（直長，台北市南北長），在寬螢幕上以 `preserveAspectRatio="...meet"` **用高度對齊**，導致左右大量留白、內容被壓在中央一條、整體偏小。
- 修法：`syncViewBox()` 把 viewBox 改成**跟著 shell 實際像素（1:1）**，並在 resize 時重設＋重新 fit；fit 的 margin 改用像素（行政區 70 / 站點 50）；`getCanvasSize()` 回傳 shell 像素。
- 因 Step C 要疊真實 TWD97 邊界，**必須維持等比縮放**（不可非等比拉伸），故水平留白（台北南北長、東西窄）為地理事實、予以保留。
- 行政區可讀性：利用閒置的水平/間距空間放大 node。`DISTRICT_NODE_RADIUS` 175→256、name/% 字級 →150（canvas 單位），name 置上、% 置下。
  - 驗證（shell 1900×704）：行政區 node 直徑 42px、名稱字高 18px（寬 37px，塞進 node）、% 14px、最近一對間距 50px（gap 8px、不重疊）。
- `clampScale` 改為相對 fit 倍率（0.4×–40×），不受 viewBox 像素尺度影響。
- 站點層級維持：fit 15px 色點不顯字；放大過 1.8× 顯示短 ID＋%（放大越多字越大）。

## 問題 3：畫面比例（node 重疊 vs 過小）

### 現況
- 畫布 8000×9202，投影 `local_equirectangular`，已用公尺正確修正緯度（長寬比地理正確，非投影失真）。
- node 半徑是**固定畫布單位**：district r=86、station r=30；rider r=14/8。
- 縮放時 node 半徑跟著等比例縮放，所以放大時 node 一起變大、密集區照樣重疊；縮小看全貌時 node 又太小。
- 現有 `fitCurrentLayer()` 只控制初始視窗，未解決 node 大小與間距的矛盾。

### 計畫（待與使用者確認方向）
- 主要方案：**node / rider / 文字改為固定螢幕大小**（半徑除以 `transform.scale`），如此放大時 node 之間實際拉開、但 node 視覺大小維持可讀；縮小時 node 不會變成看不見的點。
- 搭配調整初始 fit 的留白，使預設畫面密集區不會一開始就糊成一團。
- 此項較主觀，實作第一版後請使用者實際操作確認手感。

---

## 問題 4：疊加台北市行政區邊界

### 投影系統確認（已完成）
新增資料夾：`Taipei_district_graph/`（ESRI Shapefile）

```text
G97_A_CADIST_P.shp / .shx / .dbf / .prj / .cpg
```

- `.prj`：`TWD_1997_TM_Taiwan`，Central_Meridian 121、False_Easting 250000、Scale 0.9999、單位公尺
  → **TWD97 / TM2 zone 121（EPSG:3826）**
- `.cpg`：UTF-8（屬性表中文可正確解碼）
- 幾何：**12 個行政區 polygon**（shape type 5）
- bbox：X[296266, 317197]、Y[2761514, 2789176]（TWD97 公尺，台北市範圍）
- 屬性欄位含 `PTNAME` / `TNAME`（行政區名稱）

對照本專案座標：
- 站點 `youbike_static_info.csv`：**WGS84 經緯度（EPSG:4326）**，例如 lat 25.02605 / lon 121.5436
- 視覺化既有投影：先把 WGS84 → 本地公尺（equirectangular）→ 畫布像素

→ **兩套系統不同**：邊界是 TWD97 TM2 公尺，站點是 WGS84 度。必須先轉換才能正確疊圖。

### 注意事項
- shapefile 是 **12 個真實行政區**；視覺化目前有 **13 個區**（多了人工切出的 `臺大公館校區`）。邊界圖只會有 12 條真實界線，`臺大公館校區` 的 node 會落在真實界線內，屬正常。

### 相依套件（目前環境缺）
```text
pyshp   # 純 Python 讀 shapefile 幾何與屬性
pyproj  # TWD97 TM2 (EPSG:3826) -> WGS84 (EPSG:4326) 座標轉換
```
`requirements.txt` 目前只有 `simpy>=4.1`，需新增上述兩套件。

### 計畫
1. 新增前處理腳本（暫定 `scripts/build_district_boundaries.py`）：
   - 用 pyshp 讀 polygon 頂點（TWD97 公尺）。
   - 用 pyproj 將每個頂點 TWD97 TM2 → WGS84 lon/lat。
   - 套用與站點**完全相同**的 equirectangular → 畫布像素投影（沿用 `visualization_canvas_metadata.json` 的 lat/lon range、scale、padding），確保邊界與站點對齊。
   - 輸出 `data/processed/visualization_inputs/district_boundaries.json`（每區一組畫布座標 polyline）。
2. 視覺化新增最底層 `boundary-layer`，畫 polygon 輪廓（描邊、淡填或不填）。
3. 視為地圖底圖，永遠在最底層，不受時間播放影響。

### Step C 實作結果（已完成）
- 安裝 `pyshp`、`pyproj`（已加入 `requirements.txt`）。
- 新增 `scripts/build_district_boundaries.py`：
  - pyshp 讀 12 區 polygon 頂點（TWD97 TM2）。
  - pyproj `EPSG:3826 → EPSG:4326`（always_xy）轉 WGS84 經緯度。
  - 套用與站點**完全相同**的 local equirectangular 投影：平均緯度由 `youbike_static_info.csv` 重算，min_lon/max_lat/scale/padding 沿用 `visualization_canvas_metadata.json`。
  - 每環等距抽稀至 ≤400 點，輸出 `data/processed/visualization_inputs/district_boundaries.json`（12 區、4637 點）。
  - 12 區名稱（TNAME）全數對上 station districts，無未匹配。
- 視覺化：
  - SVG 新增最底層 `boundary-layer`；CSS `.district-boundary` 細灰線（#9aa7b4）、不填色、`non-scaling-stroke`。
  - `drawBoundaries()`：行政區層級畫全部 12 區、站點層級只畫當前區。
  - 使用者選擇：兩層級都顯示、細灰線不填色。
- 對齊驗證：各區 100% 站點落在自身邊界 bbox 內；大安區邊界 bbox 完整包住其站點 bbox（四邊略大）。臺大公館校區（人工子區）無邊界，符合預期。
- 註：邊界範圍略超出原畫布（北投北緣、內湖東緣等比最外圈站點再外推），fit 仍以 node 為準，邊界邊緣可能略超出預設視窗，可平移/縮小查看。

---

### Step D 再調整（依使用者回報）
1. **線與界線顏色分清楚**：OD 流量線（`.district-edge`、`.route-edge`）改藍 `#2f78b5`/`#4f86c0`；行政區界線改較深灰 `#7d8a98`、加粗；mesh 骨架改最淺灰 `#dde4ea`。三者分明。
2. **站點層級畫出全部 1739 站**（不分區），並**移除「區外」中繼節點**：
   - 跨行政區 rider 直接連到真實目的站（散布全圖驗證：rider X 散布 6063、Y 7538）。
   - 站點層級畫全部 12 區界線當地圖底圖。
   - mesh 改為全市地理鄰近骨架（`buildGlobalMesh`，網格加速，3234 條）。
   - 重設/fit 仍只聚焦目前選定行政區（`currentLayerPoints` 回傳該區站點），維持原縮放比例；其餘站點可平移查看。
   - 行政區拉桿保留：站點層級切換只重新 fit、不重畫。
   - 效能：建層 239ms（一次性）、`updateNodeColors` 全 1739 站 8ms、`drawRiders` 2ms。為保險仍加入著色節流（播放時約 120ms 一次）與空間網格（mesh 建構、rider 淡化判斷）。
   - 時序著色全市運作（t=0 全 50%、t=120 範圍 10%–100%）。

### Step E 微調（依使用者回報）
1. **行政區 node 改放地理中心**：原本用站點重心，改為邊界多邊形面積中心（JS `ringCentroid` + `districtNodePos`），視覺較平衡。無邊界者（臺大公館校區）退回站點重心。node 位置、行政區 OD 線端點、行政區層級 rider、fade 中心、fit 都改用 `districtNodePos`。
   - 驗證：中山區 node 由站點重心 (3675,4541) 移到邊界中心 (3796,4139)；臺大公館校區退回站點重心。
2. **行政區交界複數平行線修正**：根因是邊界抽稀讓相鄰區共用邊界取到不同點而錯開。原始資料拓樸乾淨（北投&士林共用 760 頂點），故 `build_district_boundaries.py` 預設改為**不抽稀**（full-res 15007 點）。
   - 驗證：北投&士林在畫布座標共用 760 個相同頂點 → 共用邊界精準重疊成單一線。

## Phase 7 全部完成
Steps A / A2 / A3 / B / C 皆已實作並於瀏覽器驗證。視覺化現況：
- edge 直線、node 蓋線、rider 過 node 淡化。
- node 顏色/% 隨播放時間即時變化（站點層級變化明顯）。
- 站點層級用地理鄰近 mesh 為骨架、跨區走「區外」中繼、無孤立 node。
- viewBox 跟隨視窗、node 部分反向縮放（放大可讀又解擠）、兩層級尺寸策略不同。
- 疊上台北市 12 行政區真實邊界（TWD97→WGS84→畫布，與站點對齊）。

---

## 建議實作順序（待使用者確認）

1. **問題 1（edge 直線 + 圖層順序）**：最單純、風險最低，先做。
2. **問題 2（時序著色）**：核心價值，讓動態播放真正反映狀態變化。
3. **問題 3（固定螢幕大小 node）**：手感主觀，需使用者實測回饋。
4. **問題 4（行政區邊界）**：需新增套件與前處理，獨立性高，最後做。

每完成一項就重新產生 HTML，交付使用者檢查後再進行下一項。
