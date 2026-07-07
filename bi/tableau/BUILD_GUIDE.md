# Tableau 三張儀表板 — 逐步建圖指南（Week 11 階段4）

> 目標：在 Tableau Desktop 手動連 Athena marts，做出 3 張儀表板，存成 `.twbx` + 截圖入庫。
> 本檔是**照著點**的操作手冊；資料規格在 `../README.md`。
>
> **關鍵前提（先讀，避免拉錯）**
> - `trade_date` 已是 **DATE** 型別 → Tableau 自動歸為「日期」維度。
> - `pct_change`、`breadth_pct`、`avg_pct_change`、`dividend_yield` **已經 ×100**（`5.23` = 5.23%）。
>   👉 Tableau **千萬不要**套內建「百分比」格式（會再乘 100 變 523%）。要用「數字」格式 + 自訂後綴 `%`。
> - 台股慣例 **紅漲綠跌**（與歐美相反）：`pct_change > 0` → 紅，`< 0` → 綠。
> - `code`（股號）是**字串維度**；若被自動當數值/度量，右鍵 → Convert to Dimension、必要時 → Convert to String。
> - 全部資料來源用 **Extract（.hyper）**，不要 Live（省 Athena 查詢費）。

---

## 0. 連線到 Athena（一次設定）

> ⚠️ **先裝對 driver**：Tableau 原生「Amazon Athena」連接器吃的是 **JDBC**，**不是 ODBC**。
> 若跳「No suitable driver installed, or the URL is incorrect」就是裝錯／沒裝。
> - 抓 `athena-jdbc-3.x.x-with-dependencies.jar`（<https://docs.aws.amazon.com/athena/latest/ug/jdbc-v3-driver.html>）
> - 放進 `C:\Users\<user>\Documents\My Tableau Repository\Drivers\`（OneDrive 接管則在 `...\OneDrive\Documents\...`；子資料夾不存在就手動建）
> - **完全關閉 Tableau 再重開**。
> - （ODBC driver 只有改走 `Connect → More → Other Databases (ODBC)` 才需要，本指南走原生連接器。）

1. 開 Tableau Desktop → 左側 **Connect → To a Server → Amazon Athena**。
2. 填連線視窗：
   | 欄位 | 值 |
   |------|----|
   | Server | `athena.ap-northeast-1.amazonaws.com` |
   | Port | `443` |
   | S3 Staging Directory | `s3://wendy-tw-stock-bot-marts-ap-northeast-1/athena-results/` |
   | Workgroup | `wendy-tw-stock-bot-dev` |
   | Authentication | **IAM credentials** → 填個人 Access Key ID / Secret |
3. Sign In → 左上 **Database** 選 `wendy_tw_stock_bot_dev` → 就會列出 5 張 marts。
4. **重要**：每張工作簿右上把資料來源切成 **Extract**（Data Source 頁右上角 Live/Extract 切鈕 → Extract → 存 .hyper）。

> 一個工作簿可同時連多張表：每張表 = 一個 Data Source。下面每張儀表板會標明用到哪些表。

---

## 儀表板 1：Market Overview（市場總覽）

用表：`mart_market_breadth`、`mart_top_movers`

### 前置：建計算欄位（在 `mart_market_breadth` 資料來源）
- **`Is Latest Day`**（布林，用來抓「最新交易日」KPI）
  ```
  [Trade Date] = { FIXED : MAX([Trade Date]) }
  ```

### Sheet 1-1「KPI - Advancers/Decliners/Breadth」
1. 新工作表，資料來源選 `mart_market_breadth`。
2. Filters：把 `Is Latest Day` 拉到 Filters → 勾 **True**。
3. 做 3 個大數字（BAN, Big Ass Number）：
   - Marks 卡選 **Text**；把 `advancers`（SUM）拉到 Text → 字體放大。
   - 同法再開兩個工作表分別放 `decliners`、`breadth_pct`，或用一個工作表把 3 個度量並排（Measure Values 拉到 Text）。
   - `breadth_pct`：右鍵欄位 → Default Properties → Number format → **Number (custom)**，Suffix 填 `%`，小數 2 位。
4. 顏色：`advancers` 紅、`decliners` 綠（手動設 Marks → Color）。
5. 命名工作表 `KPI_Breadth`。

### Sheet 1-2「趨勢：成交量 & 平均漲跌幅」
1. 新工作表，資料來源 `mart_market_breadth`。
2. Columns：`Trade Date`（右鍵 → **Exact Date / Continuous**，藍改綠色連續軸）。
3. Rows：`total_volume`（SUM）→ 折線圖 (Marks = Line)。
4. 再把 `avg_pct_change`（AVG 或 SUM，因每日一列用 AVG 即可）拉到 Rows → 右鍵 → **Dual Axis** → 右鍵右軸 → Synchronize 取消（兩者量級差很大，不要同步）。
5. `avg_pct_change` 軸：格式後綴 `%`。
6. Filters：`Trade Date` 拉到 Filters → **Range of dates** → Show Filter（右側日期區間篩選器）。
7. 命名 `Trend_Volume_Pct`。

### Sheet 1-3「Top Movers Top10 長條」
1. 新工作表，資料來源 `mart_top_movers`。
2. Filters：`Trade Date` → 只留最新日（或 Show Filter 讓使用者選單日）。
   - 快速法：拉 `Trade Date` 到 Filters → 選 **Relative date / 或 Latest**，或建同款 `Is Latest Day` 計算欄。
3. Rows：`Name`（股名）；Columns：`pct_change`（SUM）。
4. Marks → Color：把 `mover_type` 拉到 Color → 編輯色盤：`gainer` = 紅、`loser` = 綠。
5. 排序：點 `pct_change` 軸的排序鈕，依 pct_change 降冪 → 紅在上、綠在下。
6. `pct_change` 軸格式後綴 `%`；把 `close_price`、`volume` 拉到 Tooltip。
7. 命名 `TopMovers_Bar`。

### 組裝 Dashboard「Market Overview」
1. 底部 **New Dashboard**；Size 設 `1200 x 800`（Fixed）。
2. 版面：上排放 `KPI_Breadth`（橫向），中排 `Trend_Volume_Pct`，下排 `TopMovers_Bar`。
3. 把 `Trade Date` 區間篩選器設為 **Apply to：All using this data source**（讓趨勢圖與 KPI 連動）。
4. 加標題文字方塊：`台股市場總覽 · 資料源 Amazon Athena / dbt marts`。

---

## 儀表板 2：Stock Detail（個股走勢）

用表：`fct_daily_ohlcv`

### 前置：參數 + 計算欄位
1. 建 **Parameter**「Select Code」：右鍵資料窗空白 → Create Parameter；
   - Data type = String；Allowable values = **List** → 按 `Add from Field` 選 `code`。
2. 建計算欄位 **`Code Filter`**：
   ```
   [Code] = [Select Code]
   ```

### Sheet 2-1「收盤 + MA5/MA20」
1. 資料來源 `fct_daily_ohlcv`。
2. Filters：拉 `Code Filter` → 勾 **True**（隨參數切股票）。
3. Columns：`Trade Date`（Continuous 連續、Exact Date）。
4. Rows：`close_price`（AVG，每股每日一列用 AVG=原值）→ Line。
5. 疊 MA：把 `ma5`、`ma20` 也拉進同一軸 —— 用 **Measure Values**：
   - 拉 `Measure Names` 到 Color；Measure Values 卡只保留 `close_price / ma5 / ma20` 三項。
   - 色：close 深色、ma5 橘、ma20 藍。
6. （替代 K 線：進階，可先做折線版交作品集；K 線需 Gantt/自訂，非必要。）
7. 命名 `Price_MA`。

### Sheet 2-2「量能」
1. 同資料來源、同 `Code Filter=True`。
2. Columns：`Trade Date`（同上）；Rows：`volume`（SUM）→ Bar。
3. Marks Color：可用 `pct_change`>0 紅 / <0 綠（建計算欄 `Up Down` 見下），或單色灰。
   - **`Up Down`**：`IF [pct_change] >= 0 THEN '漲' ELSE '跌' END` → 拉到 Color，紅/綠。
4. 命名 `Volume_Bar`。

### 組裝 Dashboard「Stock Detail」
1. New Dashboard `1200 x 800`。
2. 上 2/3 放 `Price_MA`，下 1/3 放 `Volume_Bar`（兩圖共用 X 軸日期，視覺對齊）。
3. 把 **Select Code 參數**顯示出來：Price_MA 工作表 → 參數右鍵 → Show Parameter；拖到儀表板頂端。
4. 兩張工作表要共用同一個日期縮放：可各自加 `Trade Date` Range filter 並 Apply to all。

---

## 儀表板 3：Signals & Yield（訊號與殖利率）

用表：`fct_signals`、`fct_yield`

### Sheet 3-1「訊號類型分布」
1. 資料來源 `fct_signals`。
2. Filters：`Trade Date` → Show Filter（單日或區間）。
3. Columns：`signal_type`（gainer/loser/active）；Rows：`CNT(...)` → 用 **Number of Records / COUNT(code)**。
4. 或做堆疊：`Trade Date`(Columns, Continuous) × `signal_type`(Color) × COUNT(Rows)。
5. 命名 `Signal_Distribution`。

### Sheet 3-2「Score 排行」
1. 同 `fct_signals`；Filters：最新日 + `signal_type`（Show Filter）。
2. Rows：`Name`；Columns：`score`（SUM/AVG）→ Bar，依 score 降冪排序。
3. Tooltip 加 `code / close_price / pct_change`；`pct_change` 後綴 `%`。
4. 命名 `Score_Rank`。

### Sheet 3-3「殖利率 Top N 表」
1. 資料來源 `fct_yield`。
2. Filters：`Trade Date` 最新日。
3. 做**文字表**：Rows 放 `rank_no`、`Name`、`code`；Marks=Text；
   把 `dividend_yield`、`pe_ratio`、`pb_ratio` 拉到 Text（或用 Measure Values 並排成表格）。
4. 依 `dividend_yield` 由高到低排序。
   - `dividend_yield` **已是百分比數值**（如 `5.23` = 5.23%，與 `pct_change` 同款；已核對 `dividend_ingest`／webhook 輸出）→ 只需「數字 + 後綴 %」，**不要**乘 100 也不要套百分比格式。
5. 命名 `Yield_Table`。

### 組裝 Dashboard「Signals & Yield」
1. New Dashboard `1200 x 800`。
2. 左半：`Signal_Distribution` + `Score_Rank`；右半：`Yield_Table`。
3. 頂端加 `Trade Date` 篩選器，Apply to all（跨兩個資料來源需各設一次，或用 Parameter 統一）。

---

## 4. 存檔 / 截圖 / 入庫

1. **存 .twbx**（含 Extract 的封裝檔）：File → Export Packaged Workbook →
   存到 `bi/tableau/tw_stock_dashboards.twbx`。
2. **截圖**：每張儀表板 → 全螢幕 → 截圖存到 `bi/tableau/screenshots/`：
   - `market_overview.png`
   - `stock_detail.png`
   - `signals_yield.png`
3. 主 `README.md`「Warm Path / BI」段落嵌入這 3 張截圖當作品集展示。
4. （選）**Tableau Public**：File → Save to Tableau Public → 取得公開連結貼進履歷／作品集。
   - ⚠️ Tableau Public 會把資料公開；用 Extract 且只放去識別化的台股公開數據，OK。

---

## 5. 常見陷阱速查

| 症狀 | 原因 / 解法 |
|------|------------|
| No suitable driver installed, or the URL is incorrect | 裝成 ODBC 了；原生連接器要 **JDBC** jar 放 `My Tableau Repository\Drivers\`，重開 Tableau（見第 0 節） |
| 漲跌幅顯示 523% | 誤套百分比格式；`pct_change` 已 ×100，改「數字 + 後綴 %」 |
| 股號變成加總的數字 | `code` 被當度量；右鍵 → Convert to Dimension |
| 每次點都很慢/計費 | 用了 Live；切成 **Extract** |
| 紅綠顛倒 | 依台股慣例手動設 `>0` 紅、`<0` 綠，勿用預設色盤 |
| KPI 抓到舊日期 | `Is Latest Day` LOD 用 `{FIXED : MAX([Trade Date])}`，別用 relative date |
| 兩軸量級差爆掉 | Dual Axis 後**取消 Synchronize Axis** |
| marts 沒資料/表看不到 | 先確認 dbt build 有跑過且當天 ETL 有落 S3；可先用 AWS CLI 驗 Athena（見 README 資料刷新段） |
