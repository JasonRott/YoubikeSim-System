# Phase 8 真實 Capacity / Initial Bikes 與模擬數據報告

建立日期：2026-05-29
狀態：已完成並以 2 小時 smoke test 驗證。**系統完全建好前只跑短時長，不跑全時長。**

## 1. 真實 capacity / initial bikes

新增前處理：

```text
scripts/build_station_capacity_inputs.py
-> data/processed/station_capacity/station_capacity.json
-> data/processed/station_capacity/station_capacity_build_report.md
```

來源與規則：

- **capacity**：`youbike_dynamic_2026-04-28.csv` 的 `Quantity`（每站總停車格；當日各快照固定不變，取一值）。
- **initial bikes**：`round(capacity × avg_fill_ratio)`，`avg_fill_ratio` 來自 `kpis_1.csv`，clamp 到 `[0, capacity]`。
- **benchmark**：保留 `kpis_1.csv` 的 `bike_avail_rate`（見車率）、`dock_avail_rate`（見位率）。

結果：

```text
station_count = 1739（與 static 完全對齊，無缺漏）
capacity 7 ~ 99，平均 28.29
initial bikes 平均 10.42（比舊假設 15 低，更貼近真實）
```

`scenarios/real_system_scenario.py` 已改為逐站採用真實 capacity / initial：

- 新增 `--capacity-json`（預設指向上述檔案）；檔案不存在時退回 `--station-capacity` / `--initial-bikes` 假設值。
- `build_real_model` 改為 `capacity_by_station` / `initial_by_station` 逐站建立 `Station`。
- summary 移除單一假設值，改記 `uses_real_capacity` 與 `capacity_stats`（min/max/mean）。

## 2. 每次模擬的數據結果報告

新增：

```text
src/youbike_sim/run_metrics.py
```

每次跑完模擬會：

- 由 `events.csv` 的 rental/return（含 `bikes_after`）重建每站時間加權佔用率。
- 計算指標：
  - 全系統平均填充率（時間加權）。
  - 模擬見車率（≥1 台車的時間比例）、見位率（≥1 空格的時間比例），並對照真實 benchmark。
  - 曾少於 3 台車的站點數、曾無車、曾滿站站點數。
  - 排名：填充率最高/最低、缺車（<3 台）時長最長、最忙（借＋還）站點。
  - 各行政區平均填充率。
- 產生**文字敘述分析**（中文）。
- 跑完**直接印到 console**；同時寫入 `report.md`（數據結果分析＋系統指標＋排名表）與 `summary.json` 的 `metrics`。

## 3. 2 小時 smoke 驗證（weekday, seed 20260529）

```text
report/real_system_20260529_233359_weekday_seed20260529/
```

重點數據：

```text
平均填充率 37.6%（真實 avg_fill_ratio 約 38%，合理）
模擬見車率 99.8%（真實 95.7%）
模擬見位率 100.0%（真實 98.2%）
曾 <3 台車站點 86、曾無車 22、曾滿站 1
```

合理性說明：

- 平均填充率與真實 benchmark 接近，因 initial 以真實 `avg_fill_ratio` 設定。
- 模擬見車/見位率略高於真實，符合預期：僅 2 小時短窗、且尚無調度與長時間累積失衡。

## 4. weekday / weekend arrival rate 切換

來源：使用者上傳的 `stations_arrival_rates_weekday.csv`、`stations_arrival_rates_weekend.csv`
（取代舊 `stations_arrival_rates_2hr.csv`；兩者 lambda 值不同）。

```text
scripts/build_arrival_rate_inputs.py  (套用 real_system_cleaning_rules.json)
-> data/processed/arrival_rates_weekday_clean/   (1739 站)
-> data/processed/arrival_rates_weekend_clean/   (1739 站)
```

runner 切換：

- `--profile` 同時決定 OD transition matrix 與 arrival rate。
- `--arrival-dir` 預設改為 None；未指定時自動解析為 `arrival_rates_{profile}_clean`。
- `summary.json` 新增 `arrival_rate_source` 記錄實際使用的目錄。

驗證（各 2hr smoke, seed 20260530）：

```text
weekday: arrival_rates_weekday_clean, arrivals 3698, mean_fill 37.78%
weekend: arrival_rates_weekend_clean, arrivals 5498, mean_fill 37.16%
```

兩 profile 的最缺車站點清單明顯不同，確認切換生效；weekday/weekend 在同一時段需求差異也反映在 arrivals 數。

## 5. 已知限制與後續

- 「缺車 <3 台時長」排名目前由小容量站（cap 7–12）主導——這些站初始本就 <3 台、活動少，整段都低於門檻。未來可考慮相對門檻（如 <10% 容量）或分容量級距比較。
- 見車/見位率要逼近真實，需更長模擬時間累積失衡，並加入調度政策後再比較。
- travel time 仍是直線距離 / 固定速度近似。
- 全時長（一週）效能測試與 event log 尺寸控制尚未做（依使用者要求，系統建好前不跑全時長）。
