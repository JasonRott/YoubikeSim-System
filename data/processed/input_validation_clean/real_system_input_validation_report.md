# Real System Input Validation Report

本報告由 `scripts/validate_real_system_inputs.py` 產生。

## 1. Rate 有但 Static 沒有的站點與排除紀錄

目前輸入中 rate 有但 static 沒有的站點數：0
已依清理規則排除的站點數：4

已排除站點：

- 500105088：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料；週間 OD 中僅有少量 origin count。
- 500108127：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料，且目前 OD 中未觀察到。
- 500108186：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料，且目前 OD 中未觀察到。
- 500110108：rate 檔有完整 lambda，但 static 檔缺少座標與站點資料，且目前 OD 中未觀察到。

評估：

- 已排除的站點缺少 static 位置與行政區資料，因此不能建立 `Station`，也不能放入真實座標視覺化。
- 第一版正式模擬會使用清理後資料；若之後能補齊 static 資料，再重新納入。

## 2. Current Baseline 採納檢查

- static station 數：1739
- rent lambda station 數：1739
- static 有但 rent lambda 沒有的站點數：0
- current-compatible model 建立狀態：passed
- current-compatible demand generator smoke test：passed
- exact route model 建立狀態：passed
- exact route demand generator smoke test：passed
- smoke test 使用站點：500101001, 500101002, 500101003, 500101004, 500101005

可直接採納：

- `Station` 可由 `youbike_static_info.csv` 建立。
- `demand_generator` 可直接使用 `hourly_rent_lambda_by_station.json`。
- `DummyNode` 可使用 district-level `inter_district_transition` 與 flat `inbound_station_transition_by_district` 建立近似 routing。

已補上：

- `BaselineModel` 現在可接受自訂 `route_planner`。
- `StationExitRoutePlanner` 可使用 station-first matrix：起點站先選同區目的站或 `__OUT_OF_DISTRICT__`。
- exact route smoke test 已確認真實 OD matrix 可被核心 Rider 流程採納。

仍需擴充：

- 尚未建立完整大規模 scenario runner。
- 尚未接入真實 station capacity 與 initial bikes。
- 若使用未清理 transition matrix，route planner 會排除沒有 static registry 的目的行政區；clean matrix 已先套用 district alias。

## 3. Transition Matrix 與 Static Station 對齊

### weekday

- transition 中 station id 數：1597
- transition 有但 static 沒有的 station id 數：0
- static 有但 transition 沒有的 station id 數：142
- transition 有但 static 沒有的行政區數：0
- transition 有但 static 沒有的行政區：(無)

### weekend

- transition 中 station id 數：1062
- transition 有但 static 沒有的 station id 數：0
- static 有但 transition 沒有的 station id 數：677
- transition 有但 static 沒有的行政區數：0
- transition 有但 static 沒有的行政區：(無)
