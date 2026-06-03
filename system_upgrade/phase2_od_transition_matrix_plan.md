# Phase 2 OD Transition Matrix 設計討論

建立日期：2026-05-27  
狀態：已完成第一版 transition matrix 轉換腳本與輸出  
資料來源：

- `週間起訖站點統計_202512.geojson`
- `週末起訖站點統計_202512.geojson`

## 1. OD GeoJSON 資料盤點

兩份檔案皆為 `FeatureCollection`，核心欄位如下：

```text
on_stop_id
off_stop_id
on_stop
off_stop
sum_of_txn_times
district_origin
district_destination
```

### 週間資料

- feature 數：20,680
- 起點站數：1,608
- 終點站數：1,597
- 行政區數：13
- 總交易數：2,213,306
- 起點站 = 終點站交易數：241,087
- 起點站 = 終點站交易比例：約 10.9%

### 週末資料

- feature 數：4,050
- 起點站數：1,193
- 終點站數：1,166
- 行政區數：13
- 總交易數：336,154
- 起點站 = 終點站交易數：75,230
- 起點站 = 終點站交易比例：約 22.4%

## 2. 建議的 transition matrix 拆解方式

本專案不建議直接建立完整 station-to-station OD matrix。

原因：

- 1600+ 站點若直接建立完整 OD，理論上可能接近數百萬個 pair。
- 大量 pair 會是 0 或資料稀疏。
- 模型可解釋性會下降，也會增加資料清理與抽樣成本。

建議維持目前 Dummy Node 架構，把 OD 拆成兩層。

## 3. 第一層：行政區之間 transition matrix

用途：

當 rider 從起點站成功租車後，先根據起點站所屬行政區，抽出目的行政區。

定義：

```text
P(destination_district = Y | origin_district = X)
= X 區到 Y 區的總交易數 / X 區所有出發交易數
```

資料聚合方式：

```text
group by district_origin, district_destination
sum sum_of_txn_times
normalize within district_origin
```

輸出概念：

```python
inter_dist_prob = {
    "大安區": {
        "大安區": 0.62,
        "信義區": 0.12,
        "...": ...
    },
    "...": ...
}
```

此 matrix 對應到 `DummyNode.inter_dist_prob`。

## 4. 第二層：目的行政區內部 station transition matrix

用途：

當目的行政區已經抽出後，再決定要去該行政區內哪一個實體站點還車。

建議定義：

```text
P(destination_station = j | origin_station = i, destination_district = Y)
= i 到 j 的交易數 / i 到 Y 區所有站點的交易數
```

這比單純使用：

```text
P(destination_station = j | destination_district = Y)
```

更好，因為它保留了「不同起點站會偏好不同目的站」的資訊。

輸出概念：

```python
intra_dist_prob_by_destination_district = {
    "大安區": {
        "500101027": {
            "500101022": 0.42,
            "500101015": 0.18,
            "...": ...
        },
        "...": ...
    },
    "...": ...
}
```

此 matrix 對應到 `DummyNode.intra_dist_prob` 的 nested 格式。

## 5. 起點站 = 終點站 OD 的待決策問題

目前資料中同站借還比例不低：

- 週間：約 10.9%
- 週末：約 22.4%

可能解釋：

1. 使用者真的短時間騎回同站。
2. 資料聚合造成短程活動集中在同站。
3. 可能包含異常或特殊用途。

待討論處理方式：

### 方案 A：保留

優點：

- 完整保留觀察資料。
- 週末高比例同站借還也許是真實使用型態。

缺點：

- 在模擬中會出現 rider 借車後回到原站。
- 若旅行時間處理不當，可能造成太多短循環。

### 方案 B：排除後重新 normalize

優點：

- 模型比較專注在站點間移動。
- 避免同站 OD 對 transition matrix 造成過大影響。

缺點：

- 會刪除 10% 到 22% 的觀察需求。
- arrival rate 若仍使用原始總量，會造成目的地分布被改寫。

### 方案 C：獨立建模為短程返回行為

優點：

- 保留同站行為，又不讓它污染一般 OD transition。
- 可以設定較短旅行時間或特殊行為類型。

缺點：

- 模型多一層複雜度。
- Phase 2 初期可能超出必要範圍。

目前決策：

先不直接刪除同站 OD，也不把它混入一般移動 transition matrix。
第一版轉換腳本會把起點站 = 終點站的交易獨立輸出成 `*_self_station_records.csv`。

原因：

- 同站借還比例不低，週間約 10.9%，週末約 22.4%，直接刪除會讓需求量與真實行為脫鉤。
- 同站借還可能代表壞車立即歸還、取消租借、或真實短程來回；它們不是完全沒有意義的雜訊。
- 若直接放進一般 OD，會讓 transition matrix 產生大量 self-loop，使一般站點間移動被稀釋。

後續建議把同站借還接成獨立行為模組，例如：

```text
Rider arrives -> rent succeeds -> short self-return behavior -> return same station
```

這樣可以保留 bike 被短暫占用的效果，也不會污染一般 OD 移動。

## 6. 週間與週末的使用方式

建議分開建立兩套 transition matrix：

```text
weekday_inter_dist_prob
weekday_intra_dist_prob
weekend_inter_dist_prob
weekend_intra_dist_prob
```

模擬時依照日期切換：

- 週一到週五：使用 weekday matrix。
- 週六到週日：使用 weekend matrix。

若未來 arrival rate 也分週間/週末，OD matrix 與 arrival rate 應同步切換。

## 7. 已完成的轉換流程

已新增轉換程式：

```text
scripts/build_transition_matrices.py
```

目前輸出：

```text
data/processed/transition_matrices/
  weekday_station_exit_transition_by_district.json
  weekday_inter_district_transition.json
  weekday_inbound_station_transition_by_district.json
  weekday_inbound_station_transition_by_od_district.json
  weekday_self_station_records.csv
  weekend_station_exit_transition_by_district.json
  weekend_inter_district_transition.json
  weekend_inbound_station_transition_by_district.json
  weekend_inbound_station_transition_by_od_district.json
  weekend_self_station_records.csv
  matrix_build_report.md
```

### 第一版轉換邏輯

1. `station_exit_transition_by_district`
   - 每個行政區有一份 station-level matrix。
   - 每列是起點站。
   - 欄位是同行政區內目的站點，外加 `__OUT_OF_DISTRICT__`。
   - 這對應到「站點先決定是留在同區，還是進入區外 dummy node」。

2. `inter_district_transition`
   - 只使用跨行政區 OD。
   - 每列是起點行政區。
   - 欄位是目的行政區。
   - 這對應到「進入 dummy node 後選目的行政區」。

3. `inbound_station_transition_by_district`
   - 只使用跨行政區進入該區的 OD。
   - 每列是目的行政區。
   - 欄位是目的站點。
   - 這對應到「進入目的行政區後，抽目的站點」。

4. `inbound_station_transition_by_od_district`
   - 比上一項更細。
   - 保留 origin district -> destination district -> destination station。
   - 未來若要讓不同來源行政區對同一目的行政區有不同站點偏好，可以使用這份。

### 驗證結果

2026-05-27 已檢查所有 transition row 的機率總和：

- 週間 `station_exit_transition_by_district`：1572 rows，bad rows = 0。
- 週間 `inter_district_transition`：13 rows，bad rows = 0。
- 週間 `inbound_station_transition_by_district`：13 rows，bad rows = 0。
- 週間 `inbound_station_transition_by_od_district`：56 rows，bad rows = 0。
- 週末 `station_exit_transition_by_district`：997 rows，bad rows = 0。
- 週末 `inter_district_transition`：13 rows，bad rows = 0。
- 週末 `inbound_station_transition_by_district`：13 rows，bad rows = 0。
- 週末 `inbound_station_transition_by_od_district`：46 rows，bad rows = 0。
