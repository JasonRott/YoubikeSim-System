# Phase 3 Arrival Rate Input 轉換報告

建立日期：2026-05-27  
狀態：已完成第一版 2 小時 arrival rate 轉 hourly lambda 轉換

## 1. 資料來源

輸入檔：

```text
stations_arrival_rates_2hr.csv
```

欄位：

```text
sno
time_period
lambda_rent_hr
lambda_return_hr
```

其中 `lambda_rent_hr` 與 `lambda_return_hr` 的單位已經是「每小時到達率」。

## 2. 轉換邏輯

原始資料是 2 小時區間，但 baseline `demand_generator` 使用 hourly lambda。

因此轉換方式是把同一個 2 小時區間的 lambda 複製到該區間涵蓋的兩個小時。

範例：

```text
00:00-02:00, lambda_rent_hr = 5.8
=> hour 0 = 5.8
=> hour 1 = 5.8
```

## 3. 新增檔案

新增轉換腳本：

```text
scripts/build_arrival_rate_inputs.py
```

新增模型讀取工具：

```text
src/youbike_sim/inputs.py
```

並在 package export 中加入：

```python
load_hourly_lambda_by_station
get_station_hourly_lambda
```

## 4. 輸出檔案

輸出資料夾：

```text
data/processed/arrival_rates/
```

目前包含：

```text
hourly_rent_lambda_by_station.json
hourly_return_lambda_by_station.json
hourly_source_period_by_station.json
hourly_arrival_rates_long.csv
arrival_rate_metadata.json
arrival_rate_build_report.md
```

## 5. 資料品質檢查結果

```text
原始 row 數：20916
rate 檔站點數：1743
static 檔站點數：1739
缺少 hourly lambda 的站點數：0
缺少 2 小時區間的站點數：0
最大 lambda_rent_hr：57.15
最大 lambda_return_hr：72.94
```

rate 檔有但 static 檔沒有的站點 ID：

```text
500105088
500108127
500108186
500110108
```

static 檔中的 1739 個站點皆有對應 arrival rate。

## 6. 驗證

已完成以下檢查：

- `scripts/build_arrival_rate_inputs.py` 通過 `py_compile`。
- `src/youbike_sim/inputs.py` 通過 `py_compile`。
- `hourly_rent_lambda_by_station.json` 可被 `load_hourly_lambda_by_station()` 正常讀取。
- 站點 `500101001` 的展開結果符合原始資料：
  - hour 0 = 5.8
  - hour 1 = 5.8
  - hour 8 = 43.18
  - hour 9 = 43.18

## 7. 後續工作

下一步尚未把 hourly arrival rate 接進完整大規模 scenario。
目前只完成「資料轉換」與「模型端讀取工具」。

後續可新增：

- 從 `youbike_static_info.csv` 建立完整 1739 站點網路。
- 對每個站點用 `get_station_hourly_lambda()` 取出自己的 `nspp_lambda_dict`。
- 對每個站點啟動一個 `demand_generator(env, station, station_lambda)`。
