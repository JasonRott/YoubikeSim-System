# 參考資料

本檔收錄專案設計過程中參考過的研究與資料來源。每筆下面附**極簡短註解**，
說明我們參考了它的哪個部分。之後每次有新的參考資料，都要補進這裡。

---

## 旅行時間 / 繞路係數（circuity）

- **Giacomin & Levinson (2015), Road network circuity in metropolitan areas.**
  https://journals.sagepub.com/doi/10.1068/b130131p
  → circuity（道路/直線距離）概念與都市量值來源；支持我們用繞路係數放大直線距離。

- **Mennicken, Lemoy & Caruso (2024), Road network distances and detours in Europe.**
  https://journals.sagepub.com/doi/10.1177/23998083231168870
  → 歐洲都市平均徑向繞路約 1.343；作為我們繞路係數均值 ~1.3 的依據。

- **Ballou et al., Selected country circuity factors for road travel distance estimation.**
  https://www.sciencedirect.com/science/article/abs/pii/S0965856401000441
  → 各國 circuity 介於 1.12–2.10；說明繞路係數的合理範圍與變異。

- **Transportation Geography and Network Science / Circuity (Wikibooks).**
  https://en.wikibooks.org/wiki/Transportation_Geography_and_Network_Science/Circuity
  → circuity 計算方法與「都市約 1.2 倍」基準；自行車/步行通常高於開車。

## 同站來回（round trip）與短程/誤借行為

- **Uncovering round-trip patterns in bicycle sharing（ScienceDirect, S0966692325002571）。**
  https://www.sciencedirect.com/science/article/abs/pii/S0966692325002571
  → 同站來回約占 **11%**（與我們週間 10.9% 吻合）；**租借時長更長：來回 40.35 分 vs 單程 22.47 分**；
  多為購物、用餐、休閒（非通勤）。據此把同站「真正使用」校準到較長的 ~40 分。

- **Capital Bikeshare / Divvy / Citi Bike / Toronto 公開資料說明（false start 過濾）。**
  https://capitalbikeshare.com/system-data
  → 業界普遍把**< 60 秒**的行程視為「false start／使用者重新插回確認上鎖」而過濾掉；
  作為「誤借/壞車立刻歸還」族群的時間界定依據（< 60 秒）。

- **Understanding bike-sharing users' willingness to repair damaged bicycles（ScienceDirect, S096585642030731X）。**
  https://www.sciencedirect.com/science/article/abs/pii/S096585642030731X
  → 逾 73% 使用者曾遇到壞車（另一研究某系統約 17% 車輛不可用）；支持「遇到壞車→立刻換車/歸還」的存在。

## 需求審查（censored demand）與調度校準

- **Estimating Censored Spatial-Temporal Demand with Applications to Shared Micromobility (arXiv 2303.09971).**
  https://arxiv.org/pdf/2303.09971
  → 觀測 trip 是被站點空/滿審查後的需求下界；支持我們「借不到即流失」與校準觀點。

- **Xu & Jaillet, A Locational Demand Model for Bike-Sharing (MIT).**
  https://web.mit.edu/jaillet/www/general/SSRN-id3311371.pdf
  → 借車登記地點 ≠ 真實使用者位置，觀測需求有偏差；佐證需求估計的限制。

- **Excess demand prediction for bike sharing systems (PLOS ONE).**
  https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0252894
  → 空站未被滿足的「超額需求」不會出現在 trip 紀錄；與我們流失設定一致。

- **Simulation study on the fleet performance of shared autonomous bicycles (arXiv 2106.09694).**
  https://arxiv.org/pdf/2106.09694
  → 無調度下的系統表現與使用率；支持「無調度退化」的反事實模擬精神。

- **A Dynamic Approach to Rebalancing Bike-Sharing Systems (PMC5856052).**
  https://pmc.ncbi.nlm.nih.gov/articles/PMC5856052/
  → 調度在尖峰最易失效但能恢復；對照我們 time-to-fail 的構想。

## YouBike 使用特性（旅行時間校準）

- **臺北市政府交通局／主計處，臺北市公共自行車使用特性大數據分析（編號 106-18 等系列）。**
  https://www-ws.gov.taipei/Download.ashx?u=LzAwMS9VcGxvYWQvMzY3L3JlbGZpbGUvNDUwMDAvNzY2MzIzOS85OTM3NTUzMC0zYzIxLTRhZmQtOWY5Ny1mNjNkMzM0ZmEzMzcucGRm
  → YouBike 行程特性：**最常見租借時長 5–7 分鐘（眾數）**、平均租借 27.1 分（含持有時間、受前 30 分免費拉高）、
  官方以 **~10 km/hr** 當參考速度（「5 分鐘 ≈ 1 km」）。用來校準我們的旅行時間（有效速度 14/1.3≈10.8，與官方相符）。

## YouBike 台北資料來源

- **臺北市 YouBike 起訖站點統計（data.gov.tw 174977）。**
  https://data.gov.tw/en/datasets/174977
  → 我們 OD 來源（`起訖站點統計` GeoJSON）對應的官方資料；為「站對站交易次數」彙整，
  屬實際使用者交易（非車輛數變化），但僅彙整、無逐筆/逐時。

- **YouBike2.0 臺北市即時資訊（data.gov.tw 137993 / data.taipei）。**
  https://data.gov.tw/en/datasets/137993
  → 即時站點車輛/空位資料；對應我們的 dynamic 檔（capacity 的 `Quantity` 來源）。

- **Institute of Transportation, MOTC — Characteristics analysis of public bike trips and stations: Evidence from the open big data of Taipei City YouBike.**
  https://www.iot.gov.tw/cp-144-71367-3b534-2.html
  → 以台北 YouBike 開放大數據做 trip/station 特性分析的學術案例；可供方法與背景引用。

## 查證結論：YouBike 是否有「逐筆起訖 trip 紀錄」公開？

- 公開的是**彙整版起訖站點統計**（站對站交易次數，即我們的 OD GeoJSON）與**即時站點資料**；
  **沒有找到逐筆個別 trip（含時間戳）的官方開放資料**。
- 因此「更乾淨地重建需求」只能用彙整 OD（無逐時），無法取得逐筆 trip；
  校準仍以指標式（time-to-fail）為主要可行方法。
