# 資料夾整理計畫（✅ 已執行完成 2026-06，commit 0658147）

> 狀態：全部完成並推上 **https://github.com/JasonRott/YoubikeSim-System**（Public，181 檔 48MB）。
> 搬移、14 處路徑修正、簡報去嵌套、最終任務結果改名、.gitignore（含 .claude）、results/ 精選、冒煙測試皆完成。
> 以下為當初計畫，保留作紀錄。



> 原則：不動 `report/`（Pareto 正在寫）、不動相對路徑前不搬。執行＝搬檔 + 同步改所有相依路徑。

## 目標結構（只新增分類，不動既有 src/scenarios/scripts/data 子目錄）
```
data/
  raw/        <- youbike_static_info.csv, youbike_dynamic_2026-04-28.csv(gitignore,111MB),
                 kpis_1.csv, stations_arrival_rates_2hr.csv, _weekday.csv, _weekend.csv,
                 週間/週末起訖站點統計_202512.geojson, 臺北市YouBike2.0車輛數(20260520更新).csv
  snapshots/  <- initial_bikes_{4am,6am}_{weekday,weekend}.csv
  derived/    <- district_morning_peak_net_flow.csv
  geo/        <- Taipei_district_graph/ 整個移入（G97_A_CADIST_P.*）
  (既有) benchmark/ config/ processed/ 不動
docs/         <- Simulation Final Project Proposal.pdf（65MB，移出根目錄）
results/      <- 從 report/ 精選成果（tracked；report/ 本身 gitignore）：
                 _cont30_sc_crossover.png, _cont30_sc_analysis.txt, _final_pareto.csv,
                 母車視覺化 html（最後產生後複製一份）
README.md, requirements.txt 留在根目錄（git 慣例）
```

## 檔案搬移（source -> dest）
- Simulation Final Project Proposal.pdf -> docs/
- youbike_static_info.csv, youbike_dynamic_2026-04-28.csv, kpis_1.csv,
  stations_arrival_rates_2hr.csv, stations_arrival_rates_weekday.csv,
  stations_arrival_rates_weekend.csv, 週間起訖站點統計_202512.geojson,
  週末起訖站點統計_202512.geojson, 臺北市YouBike2.0車輛數(20260520更新).csv  -> data/raw/
- initial_bikes_4am_weekday.csv, initial_bikes_4am_weekend.csv,
  initial_bikes_6am_weekday.csv, initial_bikes_6am_weekend.csv  -> data/snapshots/
- district_morning_peak_net_flow.csv  -> data/derived/
- Taipei_district_graph/  -> data/geo/Taipei_district_graph/

## 程式相依路徑修改（搬檔同時改）
### A. 快照 CSV（執行腳本，使用者常用）-> data/snapshots/
- scripts/final_pareto.py:25  SNAP = "initial_bikes_4am_weekday.csv"  -> "data/snapshots/initial_bikes_4am_weekday.csv"
- scripts/tune_p7.py:26       SNAP = 同上
- scripts/run_viz.ps1:26      --snapshot-csv initial_bikes_4am_weekday.csv  -> data/snapshots/...
- scripts/run_final.ps1:13    --snapshot-csv initial_bikes_4am_weekday.csv  -> data/snapshots/...
- scripts/run_real_matrix.py  docstring 範例（14,15 行）-> data/snapshots/...（doc）

### B. 原始資料（build_*.py 前處理）-> data/raw/
- build_station_capacity_inputs.py:149  default=PROJECT_ROOT/"youbike_static_info.csv" -> PROJECT_ROOT/"data/raw/youbike_static_info.csv"
- build_station_capacity_inputs.py:151  youbike_dynamic_2026-04-28.csv -> data/raw/
- build_station_capacity_inputs.py:153  kpis_1.csv -> data/raw/
- build_district_boundaries.py:137      youbike_static_info.csv -> data/raw/
- build_district_boundaries.py:132      Taipei_district_graph/G97_A_CADIST_P.shp -> data/geo/Taipei_district_graph/...
- build_arrival_rate_inputs.py:351      default=Path("stations_arrival_rates_2hr.csv") -> Path("data/raw/...")
- build_arrival_rate_inputs.py:357      default=Path("youbike_static_info.csv") -> Path("data/raw/...")
- build_real_visualization_inputs.py:273 default=Path("youbike_static_info.csv") -> data/raw/
- build_transition_matrices.py:432      input_dir/"youbike_static_info.csv"（input_dir 預設 "."）：把 --input-dir 預設或此處改指 data/raw
- build_transition_matrices.py:435      input_dir.glob("*.geojson") -> 指 data/raw（--input-dir 預設改 data/raw 或 glob data/raw）
- validate_real_system_inputs.py:470    project_root/"stations_arrival_rates_2hr.csv" -> data/raw/
- validate_real_system_inputs.py:473    project_root.glob("*.geojson") -> (project_root/"data/raw").glob("*.geojson")

### C. derived -> data/derived/
- run_multiday.py:38  NETFLOW_CSV = PROJECT_ROOT/"district_morning_peak_net_flow.csv" -> PROJECT_ROOT/"data/derived/..."（run_multiday 已廢棄但仍改）

> 註：stations_arrival_rates_weekday/weekend.csv 與 臺北市YouBike2.0車輛數.csv 無程式 default 參照（僅文件/手動指令），搬後只需更新提及的 README/docstring。

## .gitignore（新建於根目錄）
```
report/
data/raw/youbike_dynamic_2026-04-28.csv
docs/Simulation Final Project Proposal.pdf
__pycache__/
*.pyc
.DS_Store
Thumbs.db
```

## GitHub 限制與決策（已定案 2026-06）
- report/ 65GB、內含數百個 >100MB events.csv -> 必 gitignore；關鍵成果改放 results/（tracked）。
- youbike_dynamic_2026-04-28.csv 111MB > 100MB 上限 -> gitignore（屬可重建原始資料）。
- **決策①目標 repo＝新開一個**（如 JasonRott/YoubikeSim-Full；用 `gh repo create` 建，與簡報 repo 分開）。
- **決策② Proposal PDF 不上傳**：仍移到 docs/ 做本地整理，但 gitignore（見上）。
- **決策③ 簡報材料/ 納入主 repo**：執行時 **移除 簡報材料/.git**（去嵌套），讓其檔案被主 repo 追蹤。
  註：遠端 JasonRott/YoubikeSim 不受影響、內容相同，無實質歷史損失。

## 執行順序（Pareto 完成後）
1. 先把 results/ 精選好（含最後母車 html）。
2. mkdir data/{raw,snapshots,derived,geo} docs results。
3. 搬檔（git mv 不適用，尚非 repo；用一般搬移）。
4. 套用上述 14 處路徑修改。
5. rm -rf 簡報材料/.git。
6. 寫 .gitignore + README 根目錄（補充新結構說明）。
7. 冒煙測試：用搬移後路徑跑一次極短模擬（--days 1 或現成 build 腳本 dry 檢查 import）確認沒踩到舊路徑。
8. git init -> add -> commit -> gh repo create 新 repo -> push。
