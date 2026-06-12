# Week 5 規劃書 — Bedrock + Claude 投資摘要（analyzer Lambda）

> 版本：v1（2026-06-12）｜接手對象：下一位實作 AI Agent（可直接依本書生成程式碼與 Terraform）
> 前置狀態：Week 3-4 ETL 已上線（dispatcher → SQS → worker 寫 Bronze/Silver/DynamoDB），
> 部署 tag `bff1a53`；`market_holidays` 已填（commit `51117e2`）。本書只規劃 analyzer，不含 LINE（Week 7-8）。

---

## 1. 目標與範圍（MVP）

每個交易日 ETL 完成後，自動產生一份**當日台股盤勢投資摘要**（繁體中文，3–5 段），
存進資料庫供後續 LINE 推播（Week 7-8）讀取。

**MVP 明確邊界：**
- ✅ 產生「全市場層級」每日摘要一則（非逐檔；逐檔留待後續迭代）。
- ✅ 由 analyzer **自行用程式算出當日訊號/統計**（漲跌家數、漲幅/跌幅/成交量前 N 名），
  再交給 Claude **改寫成自然語言**（grounding：LLM 不碰原始資料、只潤飾既有數字 → 防幻覺）。
- ✅ 摘要與訊號雙寫：DynamoDB（hot，供 LINE 即時讀）+ S3 marts（gold，供 BI/回溯）。
- ✅ 順手點亮 **GSI2 sparse 訊號索引**（系統原設計但 MVP 未用）。
- ❌ 不做技術指標回測、不做個股深度摘要、不接 LINE、不做 RAG 新聞情緒（皆後續波次）。

---

## 2. 架構定位與資料流

```
EventBridge schedule (16:00 台北, 平日)        ← 新增；晚於 ETL 15:30 約 30 分鐘確保資料已落地
        │  payload {"job":"daily-analyze"}（可選 trade_date 覆寫）
        ▼
analyzer Lambda (容器, ARM64)
   1) 解析/推導 trade_date（台北今日；沿用 dispatcher 的交易日判斷邏輯）
   2) 從 DynamoDB GSI1 撈「當日」+「前一交易日」全市場個股 (close, volume[, name])
   3) 純 Python 計算訊號與統計（見 §5）→ 寫回 DynamoDB 訊號項目（GSI2 sparse）
   4) 組「grounding facts」JSON → 呼叫 Bedrock Claude Haiku 4.5 (converse) 生成摘要
   5) 摘要 + facts 雙寫：DynamoDB（SUMMARY 項目）+ S3 marts（Parquet/JSON）
   6) 回傳統計（dispatched/score 數）供 CloudWatch 監控
```

**為何獨立一支 Lambda 而非塞進 worker：** worker 是 SQS 逐筆消費、平行且無序；摘要需要「全市場彙總」
的單次運算，職責不同。獨立 analyzer 維持單一職責、易測試、可單獨重跑回補。

**觸發方式決策（二選一，建議 A）：**
- **A（建議）EventBridge 獨立排程 16:00**：與 ETL 解耦，最低耦合、最易維運；
  缺點是固定延遲（30 分鐘緩衝）。符合低 TCO/高自動化原則。
- B 事件鏈（dispatcher 完成 → 發事件觸發 analyzer）：即時但增耦合與失敗處理複雜度。MVP 不採。

---

## 3. 觸發與時間

- 重用既有 `eventbridge-schedule` module，新增 `schedule_analyze`：
  `cron(0 16 ? * MON-FRI *)`、`timezone = Asia/Taipei`、target = analyzer Lambda。
- 同樣以 `var.lambda_image_tag != "" ? 1 : 0` 守門，與 analyzer 一起活化/休眠。
- analyzer 端**同樣做交易日防呆**（沿用 dispatcher 的 `_is_trading_day` + `MARKET_HOLIDAYS`），
  假日空跑直接 `skipped`，不呼叫 Bedrock（省成本）。
- event 支援 `{"trade_date":"YYYY-MM-DD"}` 覆寫 + `{"force":true}`，供回補/測試。

---

## 4. 資料輸入（讀 DynamoDB，非 Silver）

- 主來源 **DynamoDB GSI1**（`GSI1PK = DATE#{trade_date}`）一次撈當日全市場 ~1365 檔。
  - 既有欄位：`PK=STOCK#{code}`、`close`、`volume`（worker 已寫）。
  - 同法撈「前一交易日」DATE#{prev} 以算漲跌幅。prev 取法：用 `market_holidays`+週末規則往前推算最近交易日。
- **決策點：個股名稱 `name`**。目前 hot 項目沒存 name（只在 Bronze/Silver）。摘要要可讀需要名稱：
  - **建議方案 B（最低 TCO）**：在 worker `src/worker/app.py` 的 hot item 多寫一個 `name` 欄位
    （1 行，無新增 IAM/資源），analyzer 即可直接拿到名稱，免讀 Parquet。
    這會讓 worker image 變動 → 重新部署（src 改動走 deploy-images 自動鏈）。
  - 方案 A：analyzer 另讀當日 Silver Parquet 取 code→name。增加 S3 讀取與 pandas 相依，較重。MVP 不採。
  - ⚠️ 名稱回補：方案 B 只對「改動後新寫入」的資料生效，歷史項目無 name；MVP 可接受
    （摘要只需「當日」資料），或之後寫一次性回補。

---

## 5. 訊號計算（純 Python，grounding 的來源）

analyzer 用程式算出**確定性數字**，這是「防幻覺」的核心——LLM 只改寫，不創造數字。

逐檔計算：`pct_change = (close_today - close_prev) / close_prev`（prev 缺值則跳過該檔）。
彙總出 `facts`（建議結構）：
- `trade_date`、`total_count`（有效檔數）
- `advancers` / `decliners` / `unchanged`（漲/跌/平家數）
- `top_gainers`：漲幅前 5（code, name, close, pct_change）
- `top_losers`：跌幅前 5
- `most_active`：成交量前 5（code, name, volume, close, pct_change）
- 可選 `breadth`：漲家數佔比（市場廣度）

**寫回 DynamoDB 訊號項目（點亮 GSI2 sparse index）：**
- 對入選 top_gainers/losers/most_active 的個股，寫訊號項目：
  - `PK=STOCK#{code}`、`SK=SIGNAL#{trade_date}#{signal_type}`
  - `GSI2PK=SIGNAL#{trade_date}`、`GSI2SK=SCORE#{abs(pct_change):.4f}#STOCK#{code}`（高分在前，方便 Query 取前 N）
  - 屬性：signal_type（gainer/loser/active）、pct_change、close、volume、`ExpiresAt`（TTL，比照 worker 400 天）
- 之後查「某日所有訊號」只需 Query GSI2PK=SIGNAL#{date}，稀疏索引只含訊號 → 超省 RCU。

---

## 6. Bedrock + Claude 呼叫規格

- **模型**：Claude **Haiku 4.5**（低成本、足夠生成摘要）。
  - first-party ID：`claude-haiku-4-5`；**Bedrock 上加 `anthropic.` 前綴**。
  - ap-northeast-1（東京）多數新版 Claude 須走**跨區推論設定檔**：APAC 群組前綴 `apac.`，
    即 `apac.anthropic.claude-haiku-4-5`。**確切 inference profile ID 由實作時用
    `aws bedrock list-inference-profiles --region ap-northeast-1` 解析**，不硬編避免失效。
  - 設為 Terraform 變數 `bedrock_model_id`（預設填解析到的 APAC profile id），analyzer 讀 env。
- **API**：boto3 `bedrock-runtime` client 的 **`converse`**（統一介面、比 `invoke_model` 易維護）。
  - `system`：角色設定（台股分析助理、繁中、不得杜撰未提供的數字、若 facts 缺漏要誠實說明）。
  - `messages`：把 §5 的 `facts` JSON 當 user 輸入，要求「依這些已算好的數字寫 3–5 段繁中盤勢摘要」。
  - `inferenceConfig`：`maxTokens` ~1024、temperature 低（0–0.3，求穩定）。
  - **防幻覺**：prompt 明確「只根據提供的 facts，不得引入外部或假設數據；數字一律引用 facts 原值」。
- **前置：模型存取權**。須在 Bedrock console（ap-northeast-1）對 Claude Haiku 4.5 開啟
  **model access**，否則呼叫回 AccessDenied。列為實作 checklist 第一項。
- **Bedrock 不支援 Managed Agents / server-side tools**——本用例單次呼叫，無需。

---

## 7. 輸出去向

- **DynamoDB（hot，供 LINE 即時讀）**：摘要項目
  - `PK=SUMMARY#{trade_date}`、`SK=DAILY`、屬性 `summary_text`、`facts`(Map)、`model_id`、`generated_at`、`ExpiresAt`。
  - LINE Bot（Week 7-8）只需 GetItem 這一筆即可推播。
- **S3 marts（gold，供 BI/回溯，不過期）**：`marts/daily_summary/date={trade_date}/summary.json`
  （含 facts + summary_text + model 中繼資料）。供日後 Athena/dbt/BI 讀取趨勢。
- 兩者皆 idempotent（同日重跑覆寫同 key），支援回補。

---

## 8. Terraform Wiring（重用既有 module）

- **ECR**：在 `infra/environments/dev/main.tf` 的 `module.ecr.repositories` 加 `analyzer = {}`。
- **Lambda**：新增 `module.analyzer`（重用 `infra/modules/lambda`，比照 dispatcher 寫法）：
  - `count = var.lambda_image_tag != "" ? 1 : 0`、`image_uri = ...ecr...["analyzer"]:${tag}`。
  - timeout 較寬（~120s，含 Bedrock 往返）、memory 視 pandas 需求（若不用 pandas 可 256–512MB）。
  - env：`HOT_TABLE`、`MARTS_BUCKET`、`BEDROCK_MODEL_ID`、`MARKET_HOLIDAYS`、`TOP_N`(預設5)。
- **排程**：新增 `module.schedule_analyze`（重用 `eventbridge-schedule`），target analyzer，cron 16:00 平日。
- **deploy-images.yml**：build matrix 加入 analyzer（dispatcher/worker 已有，仿照加第三個 image）。
- **總開關**：沿用 `lambda_image_tag`，analyzer/schedule_analyze 跟著活化/休眠。

---

## 9. IAM 最小權限（analyzer 執行角色）

- `dynamodb:Query`（讀 GSI1 當日/前日）→ table + `${table_arn}/index/GSI1`
- `dynamodb:PutItem` / `BatchWriteItem`（寫訊號 + 摘要項目）→ table
- `s3:PutObject`（寫 marts 摘要）→ `${marts_bucket_arn}/*`
- `bedrock:InvokeModel`（converse 底層用此 action）→ resource 鎖定該 inference profile/foundation model ARN
  （ap-northeast-1；跨區 profile 可能需含對應區域 foundation model ARN，實作時依 list-inference-profiles 結果填）
- CloudWatch Logs 由 lambda module 自動建立。

---

## 10. 監控告警（併入本週）

- analyzer 回傳 `{ summarized: bool, signal_count, total_count }`。
- **CloudWatch Alarm**：
  - analyzer Lambda `Errors >= 1`（一次失敗就通知）。
  - 自訂 metric 或 log metric filter：`total_count` 低於門檻（如 < 1000）視為資料異常 → 告警。
- MVP 通知管道：SNS → Email（最低 TCO；LINE 告警留待 Week 7-8 共用 LINE channel）。
- 列為本週次要任務，主線（摘要生成）優先。

---

## 11. 成本估算與 TCO

- **Bedrock（主成本，但極低）**：一天一則摘要。輸入 facts ~3–5K token、輸出 ~1K token。
  Haiku 4.5 $1/1M(in)、$5/1M(out)。約 20 交易日/月 →
  in: 20×5K=100K → $0.10；out: 20×1K=20K → $0.10。**月 < $0.25**。
  （遠低於 v3 規劃書 $45 估值——當時假設逐檔/Claude 3；MVP 全市場單則摘要成本可忽略。）
- **Lambda**：一天一次、ARM64、數秒 → 幾乎落在免費額度內。
- **DynamoDB**：按需，每日多寫 ~16 訊號 + 1 摘要 + 讀 2×1365 項 → 月成本以分計。
- **S3 marts**：每日一個小 JSON → 可忽略。
- **EventBridge / CloudWatch / SNS Email**：免費額度內。
- **結論**：Week 5 新增月成本 **< $1**，符合低 TCO 原則；主要「成本」是 Bedrock 開權限與 IAM 設定的一次性工。

---

## 12. Work Breakdown（接手 checklist，依序）

> 凡 src/ 改動 push 後由 deploy-images 自動 build/push/部署；純 infra 改動由 terraform.yml 套用。

1. **[前置]** Bedrock console（ap-northeast-1）開啟 Claude Haiku 4.5 **model access**；
   `aws bedrock list-inference-profiles --region ap-northeast-1` 取得 APAC inference profile ID。
2. **[infra]** main.tf：ecr 加 `analyzer`、新增 `module.analyzer` + `module.schedule_analyze`、
   新增變數 `bedrock_model_id`/`top_n`；先 `-var lambda_image_tag=bff1a53` plan 確認資源預期（含 analyzer image 前 count=0 行為）。
3. **[src]** `src/worker/app.py`：hot item 增寫 `name`（方案 B，1 行）。
4. **[src]** `src/analyzer/app.py`：實作 §2 流程（交易日防呆、GSI1 撈當日+前日、§5 訊號計算、
   寫 GSI2 訊號項目、組 facts、§6 converse 呼叫、§7 雙寫輸出、回傳統計）。僅標準庫 + boto3
   （若 top-N 排序不需 pandas 則 image 免裝 pandas/pyarrow，更輕更省冷啟動）。
5. **[src]** `src/analyzer/Dockerfile`（base `public.ecr.aws/lambda/python:3.12`，ARM64）。
6. **[ci]** deploy-images.yml build matrix 加 analyzer。
7. **[infra/監控]** CloudWatch Alarm（Errors + total_count 門檻）+ SNS Email（§10）。
8. **[驗證]** push → CI 全綠（`scripts/watch-ci.sh`）→ 手動 invoke analyzer（force + 指定 trade_date）→
   檢查 DynamoDB SUMMARY 項目、GSI2 訊號、S3 marts JSON、CloudWatch 無 error。
9. **[文件/記憶]** 更新 `project_tw_stock_bot_progress.md`（新 tag、新資源、Week 5 完成）。

---

## 13. 風險與決策點（給使用者拍板）

| 議題 | 建議 | 替代 |
|------|------|------|
| 觸發方式 | A：獨立排程 16:00（低耦合） | B：ETL 完成事件鏈（即時但複雜） |
| 個股名稱來源 | B：worker 增寫 name 進 hot（1 行，最省） | A：analyzer 讀 Silver Parquet（較重） |
| 摘要粒度 | 全市場單則（MVP） | 逐檔/族群（後續迭代） |
| 模型 | Claude Haiku 4.5（成本最低） | Sonnet（品質更高、成本↑5×） |
| 告警管道 | SNS Email（MVP） | 併入 LINE（Week 7-8） |

> 防幻覺是本週設計核心：**所有數字由程式算、LLM 只負責改寫**。這也是面試可講的賣點
> （LLM grounding / 防幻覺 / 低 TCO 的 AI 落地），呼應 CLAUDE.md 的職涯定位。
