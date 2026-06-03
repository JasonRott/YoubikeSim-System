# Real System Input Validation Report

本報告由 `scripts/validate_real_system_inputs.py` 產生。

## 1. Rate 有但 Static 沒有的站點

數量：4

### 500105088

- rate period 數：12
- 平均租借 lambda/hr：1.2475
- 最大租借 lambda/hr：3.0300
- 平均還車 lambda/hr：1.2258
- 最大還車 lambda/hr：2.3900
- 週間 OD origin count：43
- 週間 OD destination count：0
- 週末 OD origin count：0
- 週末 OD destination count：0
- OD 中觀察到的站名：樟新街8巷
- OD 中觀察到的行政區：文山區

### 500108127

- rate period 數：12
- 平均租借 lambda/hr：1.3217
- 最大租借 lambda/hr：2.4800
- 平均還車 lambda/hr：1.3742
- 最大還車 lambda/hr：3.1700
- 週間 OD origin count：0
- 週間 OD destination count：0
- 週末 OD origin count：0
- 週末 OD destination count：0
- OD 中觀察到的站名：(無)
- OD 中觀察到的行政區：(無)

### 500108186

- rate period 數：12
- 平均租借 lambda/hr：1.1983
- 最大租借 lambda/hr：4.0100
- 平均還車 lambda/hr：1.1325
- 最大還車 lambda/hr：4.5500
- 週間 OD origin count：0
- 週間 OD destination count：0
- 週末 OD origin count：0
- 週末 OD destination count：0
- OD 中觀察到的站名：(無)
- OD 中觀察到的行政區：(無)

### 500110108

- rate period 數：12
- 平均租借 lambda/hr：5.1992
- 最大租借 lambda/hr：16.8400
- 平均還車 lambda/hr：3.7392
- 最大還車 lambda/hr：9.5000
- 週間 OD origin count：0
- 週間 OD destination count：0
- 週末 OD origin count：0
- 週末 OD destination count：0
- OD 中觀察到的站名：(無)
- OD 中觀察到的行政區：(無)

評估：

- 這 4 個站點有完整 12 個 2 小時 arrival rate period，但缺少 static 位置與行政區資料。
- 若沒有補 static 資料，它們不能建立 `Station`，也不能放入真實座標視覺化。
- 第一版正式模擬應排除這 4 個站點，或等補齊 `youbike_static_info.csv` 後再納入。

## 2. Current Baseline 採納檢查

- static station 數：1739
- rent lambda station 數：1743
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
- 若 OD 抽到沒有 static registry 的 `臺大專區`，目前 route planner 會排除該目的行政區。

## 3. Transition Matrix 與 Static Station 對齊

### weekday

- transition 中 station id 數：1606
- transition 有但 static 沒有的 station id 數：9
- static 有但 transition 沒有的 station id 數：142
- transition 有但 static 沒有的行政區數：1
- transition 有但 static 沒有的行政區：臺大專區

### weekend

- transition 中 station id 數：1072
- transition 有但 static 沒有的 station id 數：4
- static 有但 transition 沒有的 station id 數：671
- transition 有但 static 沒有的行政區數：1
- transition 有但 static 沒有的行政區：臺大專區
