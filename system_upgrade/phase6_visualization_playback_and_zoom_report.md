# Phase 6 真實地圖視覺化縮放與播放控制報告

建立日期：2026-05-28  
狀態：已完成第一版修正

## 1. 本次修改目標

使用者回報第一版真實地圖視覺化有兩個核心問題：

- 預設 node 太小，即使放大到最大也仍不適合細部觀察。
- 缺少動態模擬視覺化最基本的播放、暫停與速度控制。

因此本次修改集中在：

- 進入行政區層級或站點層級時，自動將該圖層所有 node fit 到畫面內。
- 將原本「最大放大」接近的視覺比例改成預設可讀比例。
- 提高可放大上限，讓使用者可以進一步查看單一 node 附近狀態。
- 新增播放、暫停、時間速度選擇。

## 2. 修改檔案

```text
scripts/visualize_real_system.py
report/real_system_20260528_082142_weekday_seed20260528/real_map_visualization.html
```

`scripts/visualize_real_system.py` 是正式來源檔。  
`real_map_visualization.html` 是重新產生後的報告輸出。

## 3. 視窗與縮放邏輯

新增 HTML/JavaScript 函式：

```text
fitCurrentLayer()
currentLayerPoints()
clientPointToSvg()
clampScale()
```

設計理由：

- 行政區層級與站點層級的座標範圍不同，不能使用同一個固定縮放倍率。
- 每次切換圖層或切換行政區時，系統會重新計算該圖層 node 的 bounding box。
- 預設畫面會盡量讓該圖層所有 node 都出現在視窗內。
- 使用者仍可繼續放大到預設 fit scale 的多倍，進行細部觀察。

## 4. Node 與 Rider 尺寸調整

本次放大了 SVG 內的基礎半徑：

```text
district node radius: 34 -> 86
station node radius: 17 -> 30
district rider radius: 8 -> 14
station rider radius: 6 -> 8
```

這不是改變模擬邏輯，只是改變視覺化呈現尺度。

## 5. 播放控制

新增控制項：

```text
播放 / 暫停
速度：0.5, 1, 2, 5, 10, 30, 60 分/秒
```

播放邏輯使用 `requestAnimationFrame`，每一個 frame 依照實際經過秒數推進 simulation clock。  
當時間到達模擬終點時，系統會自動暫停。

## 6. 驗證

已完成：

- `scripts/visualize_real_system.py` 通過 `py_compile`。
- 成功重新產生 `real_map_visualization.html`。
- 靜態檢查確認 HTML 包含：
  - `play-pause`
  - `speed-select`
  - `fitCurrentLayer`
  - `baseFitScale`
  - `clampScale`
  - `clientPointToSvg`
- 使用 Node.js 解析 HTML 中的 `<script>`，確認 JavaScript 語法正確。

未完成：

- in-app browser 無法開啟 `file://`，且 `localhost` 測試被瀏覽器政策阻擋，因此本次無法完成瀏覽器截圖驗證。

## 7. 尚待改進

- 進一步觀察實際瀏覽器畫面，確認預設 fit 的大小是否符合使用者期待。
- 若 7 天模擬事件量很大，需要加入 rider event 的分段載入或 viewport culling。
- 目前 edge routing 仍是曲線偏移，尚未保證完全避開所有 node。
