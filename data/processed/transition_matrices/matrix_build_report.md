# OD Transition Matrix Build Report

本報告由 `scripts/build_transition_matrices.py` 產生。

## 同站借還處理策略

起點站 = 終點站的 OD 已獨立抽出為 `*_self_station_records.csv`，
不放入一般移動 transition matrix。這樣可以保留壞車立即歸還、取消租借、
或真實來回旅次的資訊，同時避免一般 OD 被自我循環扭曲。

## 輸出矩陣

- `*_station_exit_transition_by_district.json`：每個行政區內，起點站選擇同區目的站或 `__OUT_OF_DISTRICT__`。
- `*_inter_district_transition.json`：已離開原行政區後，Dummy Node 選擇目的行政區。
- `*_inbound_station_transition_by_district.json`：進入某行政區後，選擇目的站點的整體分布。
- `*_inbound_station_transition_by_od_district.json`：依 origin district 與 destination district 保留更細的入站分布。
- `*_self_station_records.csv`：起終點同站交易，供後續獨立建模。

## Profile 摘要

### weekday

- 總交易數：2213306
- 同站借還交易數：241087
- 同站借還比例：0.1089
- 跨行政區交易數：379622
- 排除同站後跨行政區比例：0.1925
- 同行政區且非同站交易數：1592597
- OD 內站點數：1624
- OD 內行政區數：13
- 未在 static station 檔找到的 station id 數：10

### weekend

- 總交易數：336154
- 同站借還交易數：75230
- 同站借還比例：0.2238
- 跨行政區交易數：42830
- 排除同站後跨行政區比例：0.1641
- 同行政區且非同站交易數：218094
- OD 內站點數：1226
- OD 內行政區數：13
- 未在 static station 檔找到的 station id 數：6
