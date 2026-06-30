# BI / Analytics 層（Week 11）

台股機器人的**分析展示層**：把資料湖每日累積的 OHLCV／訊號／殖利率，透過無伺服器 SQL 與 dbt 轉換成
可視覺化的 Gold marts，最後由 Tableau 呈現。與線上即時查詢（DynamoDB 熱路徑）**完全解耦**。

```
S3 Silver (curated/*.parquet + signals/yield *.json)
   └─ Athena 外部表 (partition projection，免 Glue Crawler)
        └─ dbt-athena：staging(view) → intermediate(ephemeral) → marts(table, Parquet)
             └─ Tableau (Extract) → 3 張儀表板
```

成本：Athena 按掃描量、dbt 跑在 GitHub Actions、Tableau Desktop/Public → **每月 < US$0.5**。

---

## 連線資訊（Tableau → Amazon Athena）

| 項目 | 值 |
|------|----|
| Region | `ap-northeast-1` |
| Athena Workgroup | `wendy-tw-stock-bot-dev` |
| Glue Database（Schema）| `wendy_tw_stock_bot_dev` |
| S3 staging dir | `s3://wendy-tw-stock-bot-marts-ap-northeast-1/athena-results/` |
| 認證 | 個人 IAM Access Key（**放本機，勿入庫**）|

### 步驟
1. 安裝 **Amazon Athena ODBC driver**（Tableau → Connect → Amazon Athena）。
2. 填入上表的 Region / Workgroup / S3 staging dir；認證用個人 IAM access key/secret。
3. Database 選 `wendy_tw_stock_bot_dev`，即可看到下列 marts 表。
4. 建議所有資料來源用 **Extract（.hyper）** 而非 Live，避免每次互動都打 Athena（再省查詢費）。

> 慣例：台股**紅漲綠跌**（與歐美相反）—— Tableau 色彩請反向設定。

---

## 可用的 Gold marts（dbt 產出）

| 表 | 內容 | 主要欄位 |
|----|------|---------|
| `fct_daily_ohlcv` | 個股每日 OHLCV + 均線 | code, name, trade_date, open/high/low/close_price, volume, prev_close, pct_change, ma5, ma20 |
| `mart_market_breadth` | 每日市場廣度 | trade_date, total_count, advancers, decliners, unchanged, total_volume, avg_pct_change, breadth_pct |
| `mart_top_movers` | 每日漲跌幅前 10 | trade_date, code, name, close_price, pct_change, volume, mover_type(gainer/loser), rank_no |
| `fct_signals` | 每日訊號逐列 | trade_date, signal_type(gainer/loser/active), rank_no, code, name, close_price, volume, pct_change, score |
| `fct_yield` | 每日殖利率排行 | trade_date, rank_no, code, name, dividend_yield, pe_ratio, pb_ratio |

---

## 三張儀表板規格

### 1. Market Overview（市場總覽）
- **資料表**：`mart_market_breadth`、`mart_top_movers`
- KPI：最新 `advancers` / `decliners` / `breadth_pct`
- 趨勢：`total_volume`、`avg_pct_change` over `trade_date`（折線）
- 長條：當日 `mart_top_movers` Top10 漲跌幅（依 `mover_type` 紅/綠）
- 篩選器：日期區間

### 2. Stock Detail（個股走勢）
- **資料表**：`fct_daily_ohlcv`
- 主圖：收盤 `close_price` 折線 + `ma5` / `ma20` 疊圖（或 K 線：用 open/high/low/close）
- 副圖：`volume` 量能長條
- 參數：股票代號 `code` 下拉

### 3. Signals & Yield（訊號與殖利率）
- **資料表**：`fct_signals`、`fct_yield`
- 訊號：`signal_type` 分布、`score` 排行（依 `trade_date` 篩選）
- 殖利率：`fct_yield` Top N 表（`dividend_yield` 由高到低，附 `pe_ratio`/`pb_ratio`）

---

## 產出物入庫
- `bi/tableau/*.twbx`：Tableau 封裝工作簿
- `bi/tableau/screenshots/*.png`：儀表板截圖（放主 README 展示）
- （選）Tableau Public 公開連結 → 履歷／作品集

## 資料刷新
- 擷取：每日 ETL 15:30 → analyzer 16:00（訊號）→ dividend_ingest 17:00（殖利率）落 S3。
- 轉換：GitHub Actions `dbt.yml` 每日 17:30 台北 `dbt build` 重建 marts；亦可手動 `workflow_dispatch`。
- Tableau：Extract 重新整理（手動或排程）即取最新 marts。
