# Station Capacity Build Report

本報告由 `scripts/build_station_capacity_inputs.py` 產生。

## 來源

- capacity：`youbike_dynamic_2026-04-28.csv` 的 `Quantity`（每站總停車格，當日固定）。
- initial bikes：`round(capacity * avg_fill_ratio)`，avg_fill_ratio 來自 `kpis_1.csv`。
- 見車率 / 見位率：`kpis_1.csv` 的 `bike_avail_rate` / `dock_avail_rate`，保留作為真實 benchmark。

## 摘要

- 納入站點數：1739
- capacity 範圍：7 ~ 99，平均 28.29
- initial bikes 平均：10.42
- 缺 capacity 的站點數：0
- 缺 KPI（改用 fill=0.5）的站點數：0