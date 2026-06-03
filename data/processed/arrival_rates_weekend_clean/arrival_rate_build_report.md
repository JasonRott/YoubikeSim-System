# Arrival Rate Build Report

本報告由 `scripts/build_arrival_rate_inputs.py` 產生。

## 轉換邏輯

`stations_arrival_rates_2hr.csv` 已提供每 2 小時區間的每小時到達率。
因此轉換成 hourly lambda 時，會把同一個 2 小時區間的 lambda 複製到兩個小時。

例如：

```text
08:00-10:00, lambda_rent_hr = 43.18
=> hour 8 = 43.18, hour 9 = 43.18
```

## 輸出檔案

- `hourly_rent_lambda_by_station.json`：Baseline `demand_generator` 可直接使用的租借到達率。
- `hourly_return_lambda_by_station.json`：還車到達率，暫作校準與後續驗證使用。
- `hourly_arrival_rates_long.csv`：長表格式，方便人工檢查。
- `arrival_rate_metadata.json`：轉換摘要與資料品質檢查。

## 資料摘要

- 原始 row 數：20868
- rate 檔站點數：1739
- static 檔站點數：1739
- rate 有但 static 沒有的站點數：0
- static 有但 rate 沒有的站點數：0
- 缺少 hourly lambda 的站點數：0
- 缺少 2 小時區間的站點數：0
- 最大 lambda_rent_hr：68.75
- 最大 lambda_return_hr：74.39
- 已排除站點數：4

## 已排除站點

- 500105088：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料；週間 OD 中僅有少量 origin count。
- 500108127：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料，且目前 OD 中未觀察到。
- 500108186：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料，且目前 OD 中未觀察到。
- 500110108：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料，且目前 OD 中未觀察到。
