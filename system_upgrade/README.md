# 系統升級紀錄

本資料夾用來保存本專案從 Phase 1 baseline 模型，逐步升級到可支援真實 YouBike 站點資料、OD transition matrix、以及後續動態調度政策的過程。

使用原則：

- `phase1_baseline_report.md`：保存目前已完成的 baseline 模型、簡單情境測試、視覺化工具與限制。
- `phase2_od_transition_matrix_plan.md`：保存 2025/12 週間與週末 OD GeoJSON 資料盤點，以及如何轉成行政區之間、行政區內部 transition matrix 的討論方案與第一版轉換結果。
- `phase3_arrival_rate_input_report.md`：保存每站 2 小時 arrival rate 轉 hourly lambda 的轉換邏輯、輸出檔案與驗證結果。
- `phase4_real_system_preparation_report.md`：保存真實系統模擬前的資料採納檢查、exact route planner、真實座標視覺化輸入與尚待決策事項。
- `phase5_real_system_runner_and_map_report.md`：保存第一版真實系統 runner、2 小時 smoke simulation、地圖式 HTML 視覺化與剩餘限制。
- `phase6_visualization_playback_and_zoom_report.md`：保存真實地圖視覺化的自動 fit、放大上限、播放/暫停與速度控制修正。
- `phase7_visualization_fixes_plan.md`：保存使用者回報的 4 項視覺化問題診斷與修正計畫（edge 直線化與圖層順序、時序著色、node 螢幕固定大小、台北市行政區邊界疊圖與 TWD97/WGS84 投影確認）。逐項實作，每項完成後交付檢查。
- `phase8_real_capacity_and_metrics_report.md`：保存接入真實 capacity / initial bikes（dynamic Quantity + kpis avg_fill_ratio）與「每次模擬數據結果報告」（填充率排名、缺車時長、見車/見位率對比真實 benchmark、文字敘述）。
- `architecture_narrative.md`：**系統架構脈絡檔**。以說故事方式記錄每個系統細節的決策與理由（DES 選型、Dummy Node、借不到車流失的理由、資料清理、travel time 機率化、OD origin 缺口、視覺化決策、服務指標與 time-to-fail 校準哲學、一週模擬資料量對策）。**每次設計討論都要補進此檔**。供最終統整報告引用。
- `references.md`：**參考資料檔**。收錄參考過的研究與資料來源，每筆附極簡短註解說明參考了什麼。**每次有新參考資料都要補進此檔**。
- 之後每次做重要工程改動，都要同步更新這裡對應的報告，避免只改程式但忘記留下可理解的脈絡。

OD transition matrix 與 arrival rate input 皆已完成第一版資料轉換；後續仍需接入完整大規模 simulation scenario。
