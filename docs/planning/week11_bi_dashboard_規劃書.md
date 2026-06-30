# Week 11 規劃書 — BI Dashboard（Athena + dbt + Tableau）

> 版本：v1（2026-06-30）｜接手對象：下一位實作 AI Agent（可直接依本書生成 SQL / dbt 模型 / Terraform）
> 前置狀態：ETL→worker(Bronze/Silver/Hot)→analyzer(Bedrock+Gold)→notifier→webhook→dividend_ingest 全鏈上線並端到端驗證
> （部署 tag `cc56930`，CI 全綠）。Week 6 CloudWatch 監控已上線。資料湖三層（raw/curated/marts）與 dbt scaffold 皆已存在。
> 本書為 **BI / 分析展示層** epic，建立在既有 S3 Silver(Parquet) 之上，與線上即時查詢（DynamoDB 熱路徑）解耦，
> 走 **Warm/Analytics Path**：Athena 無伺服器 SQL → dbt-athena 建 Gold marts → Tableau 視覺化。

---

## 1. 目標與範圍（MVP）

把資料湖裡每天累積的 OHLCV / 訊號 / 配息資料，變成**可對外展示的 BI 儀表板**，作為求職作品集的「資料工程 → BI 一條龍」證據。

交付三張 Tableau 儀表板：
1. **市場總覽（Market Overview）**：當日/區間漲跌家數、成交量趨勢、大盤分布、Top 漲跌幅。
2. **個股走勢（Stock Detail）**：選股後看 K 線 / 收盤趨勢 + 均線（MA5/MA20）+ 量能。
3. **訊號與殖利率（Signals & Yield）**：當日訊號排行（沿用 analyzer 算好的 signal_type/score）＋殖利率 Top N。

**設計主軸**：沿用既有「**ETL 先算好、查詢端只讀**」哲學，但本層改走**分析路徑** ——
不碰 DynamoDB 熱表，改以 **Athena 直接查 S3 Silver Parquet**，再用 **dbt-athena 物化 Gold marts**（聚合 / 衍生指標），
Tableau 只連 marts。**最低 TCO**：Athena 按掃描量計費、partition projection 免 Glue Crawler、dbt 在 CI 內跑完即止、無常駐運算。

**明確不做（後續波次）**：即時（streaming）儀表板、Tableau Server/Cloud 付費託管（用 Tableau Public 或 Desktop 本機）、
跨市場（上櫃/興櫃）、預測模型、QuickSight（評估後選 Tableau，見 §11）、把 DynamoDB 熱表納入 Athena（用既有 S3 即可）。

---

## 2. 資料源盤點（皆為**既有** S3 資產，本 epic 不新增擷取）

| 層 | Bucket | Key 樣式 | 格式 | 內容 | BI 用途 |
|----|--------|----------|------|------|---------|
| 🥈 Silver | `wendy-tw-stock-bot-curated-ap-northeast-1` | `curated/date={YYYY-MM-DD}/{code}.parquet` | Parquet | 每股每日 `code,name,trade_date,open,high,low,close,volume` | **主事實表**（OHLCV）|
| 🥇 Gold | `wendy-tw-stock-bot-marts-ap-northeast-1` | `marts/daily_summary/date={YYYY-MM-DD}/summary.json` | JSON | analyzer 摘要 + facts_json | 摘要展示（選用，非主軸）|
| 🥉 Bronze | `wendy-tw-stock-bot-raw-ap-northeast-1` | `raw/date={YYYY-MM-DD}/{code}.json` | JSON | 原始 API response | 不直接給 BI（僅回溯）|

> **關鍵優勢**：Silver 已是 **Hive 風格分區（`date=`）的 Parquet** —— Athena partition projection 可零 Crawler 直接查、掃描量最小。
> 訊號（signal_type/score）目前只寫 DynamoDB（`STOCK#{code} / SIGNAL#{date}#{type}`，TTL 會過期），**不在 S3**。
> → 見 §6「訊號資料納入分析層」的兩個選項（建議：analyzer 加寫一份 signals 到 Silver/Gold，供回溯且不過期）。
> 殖利率（`YIELD#{date}/RANKING`）同理只在 DynamoDB；MVP 可先只做 OHLCV 儀表板，殖利率/訊號列為 §6 擴充。

---

## 3. 架構定位與資料流

```
                    （既有，不改）                          （本 epic 新增）
S3 Silver: curated/date=…/*.parquet  ──►  Athena 外部表 (Glue Catalog)  ──►  dbt-athena models
   每日 worker 累積                         silver_ohlcv（partition projection）      │  staging → marts
                                                                                     ▼
                                              S3 marts/dbt/…（Gold，Parquet 物化表）
                                                                                     ▼
                                              Athena Glue Catalog: fct_daily_ohlcv 等
                                                                                     ▼
                                              Tableau Desktop/Public（Athena ODBC/JDBC）
                                                                                     │
                                              3 張儀表板 → 截圖/Tableau Public 連結放 README
```

**運算節點全為無伺服器 / 按用量**：Athena（查詢時計費）、dbt（CI runner 內執行，跑完即止）。無新增常駐 Lambda、無 EC2、無 BI Server。

**排程**：dbt 在 GitHub Actions 跑（見 §9）。可選每日 `analyzer` 之後（17:30）由 EventBridge → 小 Lambda 觸發 `dbt build`，
或更低 TCO：**GitHub Actions `schedule` cron 每日 1 次跑 dbt**（無需任何 AWS 運算）。MVP 建議後者。

---

## 4. Athena / Glue Catalog 設計（Terraform 管理）

新增 Terraform 模組 `infra/modules/athena/`（或直接在 `dev/main.tf` 加資源），交付物：

1. **Glue Catalog Database**：`aws_glue_catalog_database "analytics"` → name `wendy_tw_stock_bot_dev`。
2. **Athena Workgroup**：`aws_athena_workgroup "primary"`
   - `result_configuration.output_location = s3://<marts-bucket>/athena-results/`
   - `enforce_workgroup_configuration = true`、`publish_cloudwatch_metrics_enabled = true`
   - `bytes_scanned_cutoff_per_query`（成本護欄，例 1GB）。
3. **Silver 外部表 `silver_ohlcv`**（partition projection，免 Crawler）— 由 dbt 建 source 或 Terraform `aws_glue_catalog_table`：
   - location `s3://<curated-bucket>/curated/`
   - 欄位：`code string, name string, trade_date string, open double, high double, low double, close double, volume double`
   - 分區鍵：`date string`（對應 `date=YYYY-MM-DD`）
   - TBLPROPERTIES：`projection.enabled=true`、`projection.date.type=date`、`projection.date.format=yyyy-MM-dd`、
     `projection.date.range=2025-01-01,NOW`、`projection.date.interval=1`、`projection.date.interval.unit=DAYS`、
     `storage.location.template=s3://<curated-bucket>/curated/date=${date}/`
   - SerDe：Parquet（`org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe`）。
4. **Athena results / marts 的 IAM**：dbt 執行身分需 `athena:*Query*`、`glue:*Table/*Database/*Partition`、
   `s3:GetObject/PutObject/ListBucket`（curated 讀、marts 讀寫、athena-results 讀寫）。

> partition projection 的好處：新一天的 `date=` 目錄一出現，Athena 立即可查，**不需跑 Crawler 也不需 `MSCK REPAIR`**，零維運、零額外計費。

---

## 5. dbt-athena 模型設計

啟用既有空殼 `dbt/`，採 adapter **`dbt-athena-community`**。專案結構：

```
dbt/
  dbt_project.yml          # profile: tw_stock_bot；models marts 物化為 table(parquet) 寫入 marts bucket
  profiles.yml             # 或用 env var；region ap-northeast-1、workgroup primary、schema wendy_tw_stock_bot_dev
  models/
    staging/
      _sources.yml         # source: silver_ohlcv（指向 §4 外部表）
      stg_ohlcv.sql        # 型別轉換、date→trade_date、過濾 close<=0（停市股，比照 analyzer 既有規則）
    intermediate/
      int_ohlcv_with_ma.sql# window function 算 MA5/MA20、前日收盤、pct_change（依 code 分組、trade_date 排序）
    marts/
      fct_daily_ohlcv.sql      # 每股每日事實（含均線、漲跌幅）→ materialized table，分區 by date
      mart_market_breadth.sql  # 每日漲跌家數、平均漲跌幅、總成交量（市場總覽用）
      mart_top_movers.sql      # 每日 Top/Bottom N 漲跌幅
  tests/
    （schema.yml: not_null code/trade_date/close、unique code+trade_date、accepted_range close>0）
  macros/
```

- **物化策略**：marts 設 `materialized='table'`、`format='parquet'`、`s3_data_dir` 指向 `s3://<marts-bucket>/dbt/`。
  Tableau 連 marts 表掃描 Parquet，快又省。
- **增量（後續優化）**：MVP 全量重算（資料小）；資料變大後改 `incremental`（unique_key=code+trade_date，partition by date）。
- **資料治理賣點**：dbt tests（not_null/unique/accepted_range）+ `dbt docs` 產 lineage → 面試可講「Silver→Gold 有測試與血緣」。

---

## 6. 訊號 / 殖利率納入分析層（擴充，建議與 MVP 同批或下一階段）

訊號與殖利率目前**只在會過期的 DynamoDB**，BI 無法做歷史趨勢。兩個選項：

| 選項 | 做法 | TCO / 風險 | 建議 |
|------|------|-----------|------|
| **A（建議）** | analyzer / dividend_ingest 在算完後**加寫一份 Parquet/JSON 到 S3**（`curated/signals/date=…`、`curated/yield/date=…`），dbt 建 `fct_signals` / `fct_yield` | 每日多幾個小 PutObject，幾乎零成本；資料不過期、可回溯 | ✅ 小改既有 Lambda，價值高 |
| B | 不改 Lambda，BI 只做 OHLCV，訊號/殖利率維持只在 LINE | 零改動 | MVP 可先這樣，但儀表板少一張 |

> 採 A 時：寫入點在 `src/analyzer/app.py`（signals 已有 in-memory rows）與 `src/dividend_ingest/app.py`（yield ranking 已算好），
> 各加一段 `to_parquet → s3.put_object`，IAM 加 curated 寫權限（analyzer 目前只有 marts 寫權限，需補 curated）。

---

## 7. Tableau 儀表板設計

連線：**Tableau Desktop**（使用者已有授權與 TDS 排錯經驗）透過 **Amazon Athena ODBC/JDBC connector**（workgroup `primary`、DB `wendy_tw_stock_bot_dev`）。
發布：**Tableau Public**（免費）放可公開 demo 連結於 README；或匯出 `.twbx` + 截圖入 `bi/tableau/`。

| 儀表板 | 主要資料表 | 視覺元件 |
|--------|-----------|---------|
| Market Overview | `mart_market_breadth`, `mart_top_movers` | 漲跌家數 KPI、成交量時間序列、Top10 漲跌幅長條、日期區間篩選器 |
| Stock Detail | `fct_daily_ohlcv` | K 線（gantt/自訂）或收盤折線 + MA5/MA20、量能副圖、個股下拉參數 |
| Signals & Yield | `fct_signals`, `fct_yield`（§6-A 後）| 訊號類型分布、score 排行、殖利率 Top N 表 |

- 共用：日期區間、股票代號參數；色彩遵循紅漲綠跌（台股慣例，**與歐美相反**，面試可提）。
- 效能：Tableau 用 **Extract（.hyper）** 快照而非 Live，避免每次互動都打 Athena（再省查詢費）。

產出物入庫：`bi/tableau/*.twbx`、`bi/tableau/screenshots/*.png`、`bi/README.md`（連結 + 設計說明）。

---

## 8. 安全 / IAM

- **不新增公開端點**；Athena/Glue 皆私有，僅 dbt CI 身分與本機 Tableau（使用者 IAM）可存取。
- dbt CI 用既有 GitHub Actions OIDC role（比照現有 deploy workflow）擴充 inline policy：Athena/Glue/S3（curated 讀、marts+athena-results 讀寫）。
- Tableau 端用使用者個人 IAM access key（**放本機憑證，不入庫**，比照 CLAUDE.md「API key 一律放環境變數不寫明文」）。
- Athena workgroup 設 `bytes_scanned_cutoff_per_query` 成本護欄；marts bucket 維持 block public access（既有設定）。

---

## 9. CI/CD

- 新增 workflow `.github/workflows/dbt.yml`：
  - 觸發：`push`（path filter `dbt/**`）＋ `schedule`（每日 1 次，台北 17:30 → cron UTC `30 9 * * 1-5`，跑在 analyzer 之後）＋ `workflow_dispatch`。
  - 步驟：checkout → setup-python → `pip install dbt-athena-community` → configure AWS（OIDC）→ `dbt deps` → `dbt build`（run+test）→（選）`dbt docs generate` 上傳 artifact。
- Terraform（Athena/Glue 資源）由既有 `terraform.yml` 套用（純 infra 變更路徑）。
- **三階段防 race**：先 push 建 Glue DB/外部表（infra）→ 再讓 dbt 跑（依賴外部表存在）。

---

## 10. 成本（TCO）估算

| 項目 | 計費 | 估算（dev 規模）|
|------|------|----------------|
| Athena 查詢 | $5 / TB 掃描 | 每日資料 ~MB 級，partition projection 限縮掃描 → **月 < $0.1** |
| Glue Catalog | 前 100 萬物件儲存免費 | $0 |
| S3 marts（dbt 物化）| 儲存 + 請求 | 數十 MB → **月 < $0.1** |
| dbt 運算 | GitHub Actions 免費額度內 | $0 |
| Tableau | Desktop（已有）/ Public（免費）| $0 |
| **合計** | | **≈ 每月 < $0.5**，符合低 TCO 原則 |

> 對照若選 QuickSight：Author $24/人/月 起 → 不符 side project 成本目標，故排除（§11）。

---

## 11. 技術選型決策（已依 CLAUDE.md 原則評估）

| 選型 | 候選 | 決定 | 理由 |
|------|------|------|------|
| 查詢引擎 | **Athena** / Redshift Serverless / DynamoDB 直連 | **Athena** | 資料已在 S3 Parquet、無伺服器、按掃描計費、partition projection 零維運 |
| 轉換層 | **dbt-athena** / Glue ETL / 純 SQL view | **dbt-athena** | 既有 dbt scaffold、測試+血緣（治理賣點）、版本控管 |
| 分區管理 | **partition projection** / Glue Crawler | **projection** | 免 Crawler 排程與計費，新分區即時可查 |
| BI 工具 | **Tableau** / QuickSight / Power BI | **Tableau** | 使用者已具 Tableau+TDS 專長且在技術棧；QuickSight 月費不符 TCO；Power BI 偏 Azure |
| dbt 排程 | **GitHub Actions cron** / EventBridge+Lambda | **GHA cron** | 零 AWS 運算、與既有 CI 一致 |

---

## 12. 風險與降級

| 議題 | 風險 | 對策 |
|------|------|------|
| Tableau→Athena 連線設定 | ODBC driver / IAM 設定卡關（使用者有 TDS 排錯經驗，仍可能耗時）| 先用 Athena console 驗證 marts 表可查，再接 Tableau；備援 Tableau Public 直連或 CSV extract |
| 訊號/殖利率不在 S3 | BI 缺兩張圖 | §6-A 小改 Lambda 補寫 S3；或 MVP 先只交付 OHLCV 總覽+個股 |
| 歷史資料量 | 目前累積天數有限，趨勢圖偏短 | 接受現狀，隨每日 ETL 自然累積；K 線/均線先用既有天數 |
| partition projection 日期範圍 | range 起點寫死 | 設 `2025-01-01,NOW`，涵蓋上線至今且自動滾動 |

---

## 13. Work Breakdown（接手 checklist，依序）

> 凡 `infra/` 改動 push 後由 `terraform.yml` 套用；`dbt/` 改動由新 `dbt.yml` 套用。先 infra 建外部表，再跑 dbt。

### 階段 1 — Athena/Glue 基礎（infra）
1. **[infra]** 新增 `aws_glue_catalog_database.analytics`（`wendy_tw_stock_bot_dev`）。
2. **[infra]** 新增 `aws_athena_workgroup.primary`（output `s3://<marts>/athena-results/`、enforce、cloudwatch、bytes cutoff）。
3. **[infra]** 新增 `aws_glue_catalog_table.silver_ohlcv`（Parquet SerDe + partition projection，§4）。
4. **[infra]** 擴充 GitHub Actions OIDC role policy：Athena/Glue/S3（curated 讀、marts+athena-results 讀寫）。push（terraform 套用）。
5. **[驗證]** Athena console：`SELECT * FROM silver_ohlcv WHERE date='<近期交易日>' LIMIT 10;` 應回資料。

### 階段 2 — dbt 模型
6. **[dbt]** 建 `dbt_project.yml` / `profiles.yml`（dbt-athena-community、workgroup primary、schema 同上）。
7. **[dbt]** `staging/_sources.yml` + `stg_ohlcv.sql`（型別轉換、過濾 close<=0）。
8. **[dbt]** `intermediate/int_ohlcv_with_ma.sql`（MA5/MA20、前日收盤、pct_change）。
9. **[dbt]** `marts/fct_daily_ohlcv.sql` + `mart_market_breadth.sql` + `mart_top_movers.sql`（materialized table parquet → marts bucket）。
10. **[dbt]** `tests`（not_null/unique/accepted_range）+ `dbt build` 通過。
11. **[ci]** 新增 `.github/workflows/dbt.yml`（push path filter + schedule + dispatch）。push 驗證綠燈。
12. **[驗證]** Athena 查 `fct_daily_ohlcv` / `mart_market_breadth` 有資料。

### 階段 3 —（選）訊號/殖利率入湖（§6-A）
13. **[src]** `analyzer/app.py` 加寫 signals → `curated/signals/date=…`；`dividend_ingest/app.py` 加寫 yield → `curated/yield/date=…`。
14. **[infra]** analyzer IAM 補 curated 寫權限。
15. **[dbt]** 加 `fct_signals` / `fct_yield` source + marts 模型。
16. **[驗證]** invoke 兩支 Lambda 補資料 → Athena 查得。

### 階段 4 — Tableau 儀表板
17. **[bi]** Tableau Desktop 接 Athena（workgroup primary）；建 3 張儀表板（§7），用 Extract。
18. **[bi]** 匯出 `bi/tableau/*.twbx` + 截圖；（選）發布 Tableau Public 取公開連結。
19. **[bi]** 寫 `bi/README.md`（架構圖、連結、設計說明、紅漲綠跌等亮點）。

### 階段 5 — 文件 / 記憶
20. **[docs]** 更新主 README：新增「Analytics / BI 路徑」段與儀表板截圖。
21. **[memory]** 更新 `project_tw_stock_bot_progress.md`（Week 11 完成、新 Athena/Glue/dbt 資源、BI 連結）。

---

## 14. 決策點（給使用者拍板）

| 議題 | 建議 | 替代 |
|------|------|------|
| 交付範圍 | **先 OHLCV 兩張（總覽+個股），訊號/殖利率列階段3** | 一次含訊號/殖利率（需先改 analyzer/dividend_ingest 寫 S3）|
| BI 工具 | **Tableau Desktop + Public（免費展示）** | Power BI / QuickSight（QuickSight 有月費）|
| 訊號入湖 | **§6 選項 A（小改 Lambda 寫 S3，不過期可回溯）** | 選項 B（不改，BI 暫缺該圖）|
| dbt 排程 | **GitHub Actions cron（零 AWS 運算）** | EventBridge+Lambda 觸發（多一個常駐元件）|
| 分區管理 | **partition projection** | Glue Crawler（多排程與計費）|
