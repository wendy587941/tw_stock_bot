# Week 9 規劃書 — 配息資料領域（Dividend Data Domain）

> 版本：v1（2026-06-21）｜接手對象：下一位實作 AI Agent（可直接依本書生成程式碼與 Terraform）
> 前置狀態：ETL + analyzer（含 Bedrock fallback）+ notifier + webhook（API Gateway HTTP API）皆已上線
> 並端到端驗證（部署 tag `710dd2e`）。Week 6 CloudWatch 監控已上線。webhook 查詢介面已可擴充指令。
> 本書為**新資料領域** epic，與既有「OHLCV→訊號→摘要」管線解耦，獨立一支擷取 Lambda + 擴充 webhook 指令。

---

## 1. 目標與範圍（MVP）

為機器人補上「配息（dividend）」資料領域，透過 LINE 即時查詢三項使用者最在意的存股資訊：

1. **個股配息日 + 到帳日**：傳「配息 2330」→ 回該股除息交易日、現金股利發放日（到帳日）、每股股利。
2. **殖利率排行榜**：傳「殖利率」→ 回全市場殖利率前 N 名（代號/股名/殖利率）。
3. **依配息頻率篩選**：傳「月配/季配/年配」→ 回該頻率的股票清單（順帶標示除息日/到帳日）。

**設計主軸**：沿用既有「**擷取時先算好、webhook 只讀預算結果**」的 grounding 模式 —— 排行/清單在擷取 Lambda
預先算妥並存成單一 DynamoDB item，webhook 端只做 GetItem + 格式化，**不加 GSI、不做 runtime scan/聚合**
（最低 TCO、低延遲、數字一致）。

**明確不做（後續波次）**：除權（配股）試算、股利政策歷史回測（→ dbt/Athena Warm Path）、Flex Message 卡片美化、
跨市場（TPEx 上櫃/興櫃）配息、推播型配息提醒（除息前 N 日主動通知，屬 notifier 擴充）。

---

## 2. 資料源盤點（已於 2026-06-21 實測 TWSE OpenAPI）

| 需求 | 資料源 | 路徑（base `https://openapi.twse.com.tw/v1/`）| 關鍵欄位 | 可用性 |
|------|--------|------|---------|--------|
| 殖利率（功能2） | 個股日 本益比/殖利率/淨值比 | `exchangeReport/BWIBBU_ALL` | `Date`(民國)、`Code`、`Name`、`DividendYield`、`PEratio`、`PBratio` | ✅ 完整，每日 ~1078 筆 |
| 股利金額 + 頻率線索（功能1/3） | 股利分派情形 | `opendata/t187ap45_L` | `公司代號`、`公司名稱`、`股利年度`、`股利所屬年(季)度`、`期別`、`股東配發-盈餘分配之現金股利(元/股)`、`董事會（擬議）股利分派日` | ✅ 有，~1135 筆 |
| **除息交易日**（功能1/3） | 除權除息預告表 | ⚠️ **待確認**：`exchangeReport/TWT48U` 在 openapi v1 回 302；需改抓 TWSE 報表端點（CSV/JSON 報表 API，如 `exchangeReport/TWT48U?response=json&...`）或 MOPS | 🟡 需實作階段確認端點 |
| **現金股利發放日（到帳日）**（功能1/3） | 公司公告 / 收益分配 | ⚠️ **覆蓋不全**：TWSE OpenAPI 無統一欄位；個股多在 MOPS 公告、ETF 由發行商/TPEx 公告 | 🔴 部分查不到 → 需降級策略（§12） |

> **盤點結論**：殖利率（功能2）資料**完全到位、最快見效**；配息金額/頻率可由 `t187ap45_L` 推導；
> 「除息日/到帳日」是**資料缺口**，要在實作階段補來源並設計「查不到」的降級顯示。故交付順序 **9a → 9b → 9c**。

**民國日期轉換**（BWIBBU/t187ap45 的日期皆為民國 `YYYMMDD`）：`西元年 = 前3碼 + 1911`，
例 `1150618` → `115+1911=2026` → `2026-06-18`。實作共用一個 `_roc_to_iso()` helper（比照 dispatcher 既有日期處理）。

---

## 3. 架構定位與資料流

```
新增排程 EventBridge（平日 17:00 台北，晚於 analyzer 16:00、notifier 16:30）
        │  payload {"job":"daily-dividend"}（可選 trade_date 覆寫 + force）
        ▼
dividend_ingest Lambda（容器, ARM64）  ← 單一 Lambda、不走 SQS fan-out（全市場單檔，無需分散處理）
   1) 交易日防呆（沿用 _is_trading_day + MARKET_HOLIDAYS）
   2) 抓 BWIBBU_ALL → 算殖利率排行 top N → 寫 YIELD#{date}/RANKING
   3) 抓 t187ap45_L → upsert DIVIDEND#{code}/META（股利金額、年度、期別）
   4)（9b）抓除息預告 → 補 DIVIDEND#{code}/META 的 ex_date / pay_date（best-effort）
   5)（9c）依期別/頻率規則分類 → 寫 DIVFREQ#{freq}/LIST（monthly/quarterly/annual）
   6) 回傳統計 {yield_count, dividend_count, freq_counts}
        ▼
DynamoDB（既有單表 wendy-tw-stock-bot-hot-dev，新增 3 類 item，§5）
        ▲
        │ GetItem（webhook 既有 dynamodb:GetItem 權限已涵蓋同表所有 PK，無需改 IAM）
webhook Lambda 擴充 _route()：殖利率 / 配息<股號> / 月配・季配・年配 → 讀 item → Reply
```

**為何獨立一支 `dividend_ingest`、不掛進 dispatcher/worker**：配息資料是**全市場單檔批次**（像 STOCK_DAY_ALL），
處理輕（無逐檔技術指標計算），用一支 Lambda 直接抓+寫即可；不需 dispatcher→SQS→worker 的逐檔 fan-out。
單一職責、與 OHLCV 管線解耦，獨立排程、獨立活化。

---

## 4. 分批交付（建議依序，每批一個可上線的 PR）

| 批次 | 功能 | 內容 | 依賴 |
|------|------|------|------|
| **9a** | 殖利率排行 | dividend_ingest（只做 BWIBBU 排行）+ YIELD#item + webhook「殖利率」 | 無（資料齊，最快見效） |
| **9b** | 個股配息日/到帳日 | 擴充 ingest 抓 t187ap45 + 除息預告 → DIVIDEND# item + webhook「配息 <股號>」 | 9a（同一 Lambda） |
| **9c** | 配息頻率篩選 | 在 9b 資料上做頻率分類 → DIVFREQ# item + webhook「月配/季配/年配」 | 9b（需配息資料） |

---

## 5. 資料模型（DynamoDB 單表設計，沿用既有表）

> 既有表 key：`PK` / `SK`，GSI1（DATE 維度）、GSI2（SIGNAL sparse）。本領域**不新增 GSI**，
> 全部用 PK/SK 直查 + 預算好的彙整 item。屬性值 Decimal 轉換比照 worker/analyzer（None 屬性移除）。

### 5.1 殖利率排行（9a）
```
PK = "YIELD#{trade_date}"          例 YIELD#2026-06-18
SK = "RANKING"
top_json   = JSON 字串，list[{code, name, yield, pe, pb}]（依 yield 由高到低，取前 TOP_N，預設 20）
generated_at = ISO8601
ExpiresAt  = epoch（+400 天 TTL，比照 hot store）
```
> 用「單一彙整 item + JSON 字串」而非每檔一列：webhook 一次 GetItem 即拿到整份排行，免 Query/排序、免 GSI。

### 5.2 個股配息維度（9b）— 維度資料、覆寫更新
```
PK = "DIVIDEND#{code}"             例 DIVIDEND#2330
SK = "META"
name           = 股名
cash_dividend  = 每股現金股利（元，Decimal）        ← t187ap45「股東配發-盈餘分配之現金股利(元/股)」
dividend_year  = 股利年度（民國→西元）
period         = 期別 + 所屬年(季)度（如「年度/期別1」）  ← 供 9c 頻率推導
ex_date        = 除息交易日（ISO，nullable）          ← 除息預告來源（9b best-effort）
pay_date       = 現金股利發放日/到帳日（ISO，nullable） ← 覆蓋不全，可能 None（§12 降級）
frequency      = "monthly"|"quarterly"|"annual"|"unknown"（9c 寫入）
updated_at     = ISO8601
ExpiresAt      = epoch（+400 天）
```

### 5.3 配息頻率清單（9c）
```
PK = "DIVFREQ#{frequency}"         frequency ∈ monthly/quarterly/annual
SK = "LIST"
items_json   = JSON 字串，list[{code, name, ex_date, pay_date}]（依 ex_date 近→遠）
generated_at = ISO8601
ExpiresAt    = epoch（+400 天）
```

---

## 6. webhook 指令規格（擴充既有 `_route()`）

| 使用者輸入 | 路由 | 讀取 | 回覆格式 |
|-----------|------|------|---------|
| `殖利率`、`殖利率排行`、`yield` | 殖利率排行 | GetItem `YIELD#{latest}/RANKING` | `📈 殖利率排行｜{date}\n1. 2880 華南金 6.12%\n…` |
| `配息 2330`、`2330配息`、`2330 配息` | 個股配息 | 解析股號（regex `\d{4,6}`）→ GetItem `DIVIDEND#{code}/META` | `💰 {name}（{code}）配息\n除息日：{ex}\n到帳日：{pay}\n現金股利：{amt} 元/股` |
| `月配`、`季配`、`年配` | 頻率清單 | map→freq → GetItem `DIVFREQ#{freq}/LIST` | `🗓️ 月配清單（{n}檔）\n00929 復華台灣科技優息 除息 {ex}／到帳 {pay}\n…` |
| 其他 | 既有 help | — | 既有 HELP_TEXT 增列新指令說明 |

- 「最近一個有資料的日期」沿用 webhook 既有 `_latest_summary` 的回退模式，另寫對應 `_latest_yield()`。
- **到帳日 None 時降級**：顯示「待公告」而非空白（§12）。
- 股號解析：取訊息中第一組 4~6 碼數字；找不到 → 回提示「請輸入股號，例：配息 2330」。
- HELP_TEXT 增列：「・殖利率 → 殖利率排行」「・配息 <股號> → 除息/到帳日」「・月配/季配/年配 → 頻率清單」。

---

## 7. dividend_ingest Lambda 規格

- base image `public.ecr.aws/lambda/python:3.12`，ARM64；**純 stdlib（urllib/json）+ boto3**，無 pandas → image 輕。
- env：`HOT_TABLE`、`MARKET_HOLIDAYS`、`TOP_N`（預設 20）、（9b）除息預告來源 URL/開關。
- timeout ~120s（數個 HTTP 抓取 + 批次寫入）、memory 256–512MB。
- event：`{"force":true}` 繞過交易日檢查、`{"trade_date":"YYYY-MM-DD"}` 覆寫（回補/測試），與 analyzer 一致。
- 寫入用 `table.batch_writer()`（比照 analyzer `_write_signals`）；Decimal 轉換、None 屬性移除比照 worker。
- 失敗處理：任一資料源抓取失敗 → log WARN，已成功的部分照寫（部分降級不整批失敗）；
  可比照 analyzer 埋 log 關鍵字供 Week 6 metric filter（如 `dividend_source_failed`）。

---

## 8. IAM 最小權限

- **dividend_ingest 角色**：`dynamodb:PutItem` + `dynamodb:BatchWriteItem` → `${hot_store.table_arn}`。
  （只寫不讀；若 9b 需先讀舊 META 再 merge，另加 `dynamodb:GetItem`。）外部 TWSE 為公開 HTTP，無需 AWS 權限。
- **webhook 角色**：**無需異動** —— 既有 `dynamodb:GetItem` on `table_arn` 已涵蓋同表所有 PK（YIELD#/DIVIDEND#/DIVFREQ#）。

---

## 9. Terraform Wiring（重用既有 module）

> 沿用 analyzer/notifier/webhook 的**三階段 push**（避免 ECR repo/image bootstrap race）。

- **ECR**（`main.tf` ecr repositories）：新增 `dividend_ingest = {}`（第 6 個 repo）。
- **Lambda**：新增 `module.dividend_ingest`（重用 `infra/modules/lambda`，比照 analyzer；`count` 跟 `lambda_image_tag`）。
- **排程**：新增 `module.schedule_dividend`（重用 `eventbridge-schedule`），cron `cron(0 17 ? * MON-FRI *)` 台北、
  target dividend_ingest、input `{"job":"daily-dividend"}`、`count = length(module.dividend_ingest) > 0 ? 1 : 0`。
- **deploy-images.yml**：build matrix 加 `dividend_ingest`（第 6 image）。
- **監控**（接 Week 6）：`module.monitoring` 的 `lambda_function_names` 加入 dividend_ingest → 自動長出 Errors alarm；
  可選加 `dividend_source_failed` log metric filter + alarm（比照 bedrock_fallback）。
- **變數**：`top_n` 可共用既有變數或新增 `dividend_top_n`（預設 20）。

---

## 10. 監控告警（接 Week 6）

- dividend_ingest `Errors>=1` → 自動納入既有 SNS topic（只要把函式名加進 monitoring 模組的 map）。
- 可選 `DividendSourceFailed` 自訂指標（log filter `dividend_source_failed`）→ 來源抓取異常時通知。
- 到帳日覆蓋率可記錄成 log 數字（如 `pay_date_coverage=63%`），未來要做 Dashboard 時有資料。

---

## 11. 成本估算與 TCO

- **Lambda（dividend_ingest）**：每日 1 次、ARM64、數秒、256–512MB → 幾乎全在免費額度 → ≈ **$0**。
- **DynamoDB**：每日寫入 ~1 份排行 + ~1100 檔維度 + 3 份頻率清單（按需計費）→ 月成本以分計；
  webhook 讀取每次 1 GetItem → 可忽略。**不新增 GSI**（省 GSI 寫入放大成本）。
- **EventBridge / CloudWatch**：免費額度內。
- **TWSE OpenAPI**：公開免費。
- **結論**：Week 9 新增月成本 **≈ $0**，維持低 TCO；無新增常駐服務、無新增 GSI。

---

## 12. 資料風險與降級策略（自動化風險評估）

| 風險 | 說明 | 降級/對策 |
|------|------|----------|
| **到帳日覆蓋不全** | 現金股利發放日非 TWSE OpenAPI 統一欄位，部分個股/時點查不到 | `pay_date=None` → webhook 顯示「待公告」；以**除息日**為主要資訊；log 覆蓋率供觀測 |
| **除息日來源待確認** | openapi v1 的 TWT48U 回 302，需改報表端點或 MOPS | 實作階段先確認端點；未取得前 9b 可先只供「股利金額/年度」、ex_date 標「待公告」 |
| **頻率分類無官方欄位** | 月/季配主要是 **ETF（00 開頭）**，t187ap45 以上市公司年度股利為主，ETF 收益分配在另一來源 | 規則：依「近 12 個月配息次數」推導（>=10→月配、3~4→季配、1~2→年配）；ETF 名單可另抓收益分配資料補；不足者標 `unknown` |
| **ETF 收益分配 vs 公司股利** | 兩者資料源不同（ETF 是收益分配、個股是盈餘分配） | 9c 若要完整月配清單，需納入 ETF 收益分配來源；MVP 可先以可得資料呈現並註明涵蓋範圍 |
| **民國/西元日期** | 來源皆民國 YYYMMDD | 統一 `_roc_to_iso()` 轉換並單元測試邊界 |

> **設計賣點（面試可講）**：誠實面對資料缺口並設計降級顯示（「待公告」而非假資料），體現 **grounding／資料治理**
> 成熟度；新資料領域與既有管線**解耦**（獨立 Lambda + 同表不同 PK + 不加 GSI），展現可擴充的低 TCO 架構設計。

---

## 13. Work Breakdown（接手 checklist，依序）

> 凡 src/ 改動 push 後由 deploy-images 自動 build/部署；純 infra 改動由 terraform.yml 套用。三階段防 bootstrap race。

### 9a — 殖利率排行（先做）
1. **[infra·stage1]** `main.tf` ecr 加 `dividend_ingest`；新增 `_roc_to_iso` 不需 infra。先 push 建 repo。
2. **[src]** `src/dividend_ingest/app.py`：交易日防呆 → 抓 `BWIBBU_ALL` → 解析（民國轉換、DividendYield 空值跳過）
   → 排序取 TOP_N → 寫 `YIELD#{date}/RANKING`。`Dockerfile`（無 pandas）、`requirements.txt`。
3. **[ci]** `deploy-images.yml` build matrix 加 `dividend_ingest`。push（stage2，build image）。
4. **[infra·stage3]** `module.dividend_ingest` + `module.schedule_dividend`（17:00 平日）+ monitoring map 加入。push 套用。
5. **[src]** `src/webhook/app.py`：`_route` 加「殖利率」→ `_latest_yield()` GetItem `YIELD#…/RANKING` → 格式化。push。
6. **[驗證]** invoke dividend_ingest `{"force":true,"trade_date":"<近期交易日>"}` → 查 YIELD item；
   LINE 傳「殖利率」→ 收排行。

### 9b — 個股配息日/到帳日
7. **[資料源]** 確認除息預告/到帳日端點（§2/§12）；不可得時走降級（ex/pay 標「待公告」）。
8. **[src]** dividend_ingest 加抓 `t187ap45_L`（+除息預告）→ upsert `DIVIDEND#{code}/META`（含 cash_dividend、
   dividend_year、period、ex_date、pay_date best-effort）。
9. **[src]** webhook 加「配息 <股號>」：解析股號 → GetItem `DIVIDEND#{code}/META` → 格式化（None→待公告）。
10. **[驗證]** invoke 補資料 → LINE 傳「配息 2330」→ 收除息/到帳/股利。

### 9c — 配息頻率篩選
11. **[src]** dividend_ingest 加頻率分類（近 12 月配息次數規則 + ETF 名單）→ 回寫 `DIVIDEND#` 的 frequency
    + 彙整寫 `DIVFREQ#{freq}/LIST`。
12. **[src]** webhook 加「月配/季配/年配」→ GetItem `DIVFREQ#{freq}/LIST` → 清單格式化。
13. **[驗證]** LINE 傳「月配」→ 收清單（含除息/到帳日）。
14. **[文件/記憶]** 更新 `project_tw_stock_bot_progress.md`（Week 9 完成、新資源、資料覆蓋率現況）。

---

## 14. 風險與決策點（給使用者拍板）

| 議題 | 建議 | 替代 |
|------|------|------|
| 交付順序 | **9a 殖利率先上（資料齊、最快見效）**，再 9b/9c | 一次做完（範圍大、卡在到帳日資料風險） |
| 到帳日查不到時 | **顯示「待公告」+ 以除息日為主** | 不顯示該欄／改抓第三方來源（增相依與維運） |
| 頻率分類來源 | **近 12 月配息次數推導 + ETF 名單補** | 維護人工對照表（準但要更新）／只做個股年配先上 |
| 排行/清單儲存 | **預算好的單一彙整 item（不加 GSI）** | 每檔一列 + 新增 GSI（彈性高但 GSI 成本/複雜度上升） |
| 擷取排程時間 | **17:00 平日（晚於 notifier 16:30）** | 與 ETL 15:30 併批（耦合，較不建議） |
| ETF 月配涵蓋 | MVP 先以可得資料呈現並註明範圍 | 另接 ETF 收益分配來源做完整月配清單（後續迭代） |
