# Real Visualization Input Build Report

本報告由 `scripts/build_real_visualization_inputs.py` 產生。

## 輸出內容

- `station_short_id_lookup.csv/json`：長站點 ID 與短 ID 對照。
- `station_positions.json`：真實站點經緯度投影後的畫布座標。
- `district_positions.json`：各行政區站點重心座標。
- `visualization_canvas_metadata.json`：畫布尺寸與投影比例。

## 座標設定

- station count：1739
- district count：13
- canvas width：8000
- canvas height：9202
- padding：160
- scale px/m：0.473664
- latitude range：[24.97619, 25.14582]
- longitude range：[121.46228, 121.62306]

## 行政區重心

- 中山區：stations=200, x=3675.203, y=4541.189
- 中正區：stations=135, x=2894.931, y=6032.154
- 信義區：stations=126, x=5217.96, y=5951.732
- 內湖區：stations=217, x=6128.169, y=3916.839
- 北投區：stations=124, x=2007.902, y=1268.96
- 南港區：stations=119, x=6847.258, y=5148.122
- 士林區：stations=149, x=3021.671, y=2682.463
- 大同區：stations=83, x=2665.375, y=4455.526
- 大安區：stations=210, x=3988.721, y=6325.839
- 文山區：stations=119, x=4621.773, y=8146.156
- 松山區：stations=107, x=4782.452, y=4920.096
- 臺大公館校區：stations=60, x=3767.557, y=6888.749
- 萬華區：stations=90, x=1914.359, y=6202.38