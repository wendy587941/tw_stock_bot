# Week 7-8 規劃書 — LINE Bot 每日盤勢推播（notifier / webhook Lambda）

> 版本：v1（2026-06-15）｜接手對象：下一位實作 AI Agent（可直接依本書生成程式碼與 Terraform）
> 前置狀態：Week 5 Bedrock analyzer 已上線並端到端驗證成功（commit `efc4f38`，本機=remote，CI 全綠）。
> 每日 16:00（平日）analyzer 已把摘要寫進 DynamoDB `SUMMARY#{trade_date}/DAILY`（`summary_text` + `facts_json`）。
> 本書只規劃 LINE 推播與（選配）互動 webhook，不含技術指標回測 / 個股深度摘要 / RAG（皆後續波次）。

---

## 1. 目標與範圍（MVP）

把已生成的每日台股盤勢摘要，**每個交易日自動推播到 Wendy 的 LINE**，讓整條 pipeline
（排程 → ETL → analyzer → 摘要）收成「使用者看得到成果」的閉環。這是作品集 demo 效果最強的一塊。

**分兩階段交付（建議先做 Phase 1 收閉環，再視需要做 Phase 2）：**

### Phase 1（主線，本書重點）— 純排程推播 `notifier` Lambda
- ✅ 每交易日 analyzer 跑完後，`notifier` Lambda 讀 `SUMMARY#{date}/DAILY` → 格式化 → LINE Push API 推給指定對象。
- ✅ LINE channel access token / 推播目標 userId 存 **SSM Parameter Store SecureString**（免費、沿用既有 SSM 慣例）。
- ✅ 交易日防呆（沿用 dispatcher/analyzer 的 `_is_trading_day` + `MARKET_HOLIDAYS`），假日不推。
- ✅ 找不到當日摘要時的優雅降級（log + 不推空訊息，回 `skipped:no_summary`）。
- ✅ 純標準庫（`urllib` 呼叫 LINE API）+ boto3，不裝額外套件 → image 輕、冷啟快。

### Phase 2（選配，本書列規格但不強制本週做）— 互動 webhook
- ✅ LINE webhook（使用者傳訊 → 回覆），支援指令如「今日」「盤勢」→ 即時回當日摘要、「訊號」→ 回 GSI2 訊號前 N。
- ✅ 驗證 `X-Line-Signature`（HMAC-SHA256）防偽造請求。
- ✅ 入口用 **Lambda Function URL**（免 API Gateway 費用，最低 TCO）。
- ✅ `follow` 事件自動擷取 userId（解決 Phase 1「如何取得自己 userId」的問題）。

**MVP 明確不做：** 多使用者訂閱管理、Flex Message 精美排版（先純文字）、圖表圖片、個股訂閱、付費推播額度管理。

---

## 2. 架構定位與資料流

```
【Phase 1：推播】
EventBridge schedule (16:30 台北, 平日)   ← 新增；晚於 analyzer 16:00 約 30 分鐘確保摘要已落地
        │  payload {"job":"daily-notify"}（可選 trade_date 覆寫）
        ▼
notifier Lambda (容器, ARM64)
   1) 解析 trade_date（台北今日；交易日防呆，假日 skip）
   2) DynamoDB GetItem PK=SUMMARY#{date}, SK=DAILY → 取 summary_text (+ 可選 facts_json)
   3) 組 LINE 訊息（標題行 + 摘要本文；過長則截斷或分段）
   4) 從 SSM 讀 channel access token + 推播目標 → 呼叫 LINE Push API (urllib)
   5) 回傳 {pushed: bool, trade_date, target}

【Phase 2：互動（選配）】
LINE Platform ──webhook POST──▶ Lambda Function URL (auth_type=NONE)
        ▼
webhook Lambda (容器, ARM64)
   1) 驗 X-Line-Signature（HMAC-SHA256(channel secret, rawBody)）
   2) 解析 events[]：message(text) / follow / unfollow
   3) 指令路由：今日/盤勢→讀 SUMMARY 當日；訊號→Query GSI2；其他→help
   4) 用 replyToken 呼叫 LINE Reply API 回覆
```

**為何 notifier 與 webhook 分成兩支 Lambda：** 觸發來源、執行模型、權限完全不同——
notifier 是排程驅動、單次主動推播；webhook 是 HTTP 驅動、被動回應且需公開端點與簽章驗證。
分開維持單一職責、各自最小權限，符合既有 dispatcher/worker/analyzer 的拆分風格。Phase 1 可獨立上線。

---

## 3. 觸發與時間（Phase 1）

- 重用既有 `eventbridge-schedule` module，新增 `schedule_notify`：
  `cron(30 16 ? * MON-FRI *)`、`timezone = Asia/Taipei`、target = notifier Lambda、
  `target_input = {"job":"daily-notify"}`。
- 時序：ETL 15:30 → analyzer 16:00 → **notify 16:30**（各留 30 分鐘緩衝，沿用既有解耦排程風格）。
- 守門：`count = length(module.notifier) > 0 ? 1 : 0`（比照 `schedule_analyze`），跟 notifier 一起活化/休眠。
- notifier 端**同樣做交易日防呆**（沿用 `_is_trading_day` + `MARKET_HOLIDAYS`），假日空跑直接 skip，不呼叫 LINE。
- event 支援 `{"trade_date":"YYYY-MM-DD"}` 覆寫 + `{"force":true}`，供回補/測試（與 analyzer 一致）。

---

## 4. 資料輸入（讀 DynamoDB，單筆 GetItem）

- 主來源 DynamoDB `GetItem`：`PK=SUMMARY#{trade_date}`、`SK=DAILY`。
  - 取 `summary_text`（LINE 推播本文）；可選取 `facts_json` 做標題列數字（漲跌家數、廣度）。
  - **找不到項目**（analyzer 當日未跑/失敗）→ 不推空訊息，回 `skipped:no_summary` 並 log 警告
    （此事件可被 Week 6 CloudWatch 告警捕捉）。
- 不需讀 GSI / S3；Phase 1 只 GetItem 一筆，RCU 成本可忽略。
- **prev/比較資料不需另撈**：facts 內已含 analyzer 算好的當日彙總，直接引用（grounding 一致性已由 analyzer 保證）。

---

## 5. LINE 訊息格式（Phase 1）

純文字 message（`type: text`），MVP 不用 Flex。建議結構：

```
📊 台股盤勢摘要｜{trade_date}（{星期}）
漲 {advancers}　跌 {decliners}　平 {unchanged}　市場廣度 {breadth_pct}%
───────────────
{summary_text}
```

- 標題列數字直接取自 `facts_json`（已是 analyzer 精算值，**不重新計算、不交 LLM**，維持 grounding 一致）。
- **長度**：LINE 單則 text 上限 5000 字元；3–5 段摘要遠低於上限，正常無需分段。
  仍應實作防呆：若 `header + body > 4900` 則截斷本文並加「…（全文見資料庫）」。
- emoji 與分隔線純為可讀性；不影響資料正確性。
- **未來迭代**：Flex Message（卡片式，含漲跌色塊 + top gainers 表格）、附 Athena/QuickSight 圖表連結。

---

## 6. LINE Messaging API 呼叫規格

- **SDK 決策：不裝 `line-bot-sdk`，用標準庫 `urllib.request` 直呼 REST API**
  （避免額外相依、image 更輕、冷啟更快；呼叫單純，stdlib 足夠）。
- **Push（Phase 1）**：`POST https://api.line.me/v2/bot/message/push`
  - header：`Authorization: Bearer {channel_access_token}`、`Content-Type: application/json`
  - body：`{"to": "{target_id}", "messages": [{"type":"text","text":"..."}]}`
  - `target_id`：Wendy 個人 userId（1-on-1）或群組 groupId（推給特定群）。MVP 用個人 userId。
  - 回應非 2xx 要 raise → 讓 Lambda 標記失敗（供告警）。
- **Reply（Phase 2）**：`POST https://api.line.me/v2/bot/message/reply`，body 加 `replyToken`（webhook event 內，限時 ~1 分鐘、一次性）。
- **錯誤處理**：429（額度）/ 5xx 應 log 並回失敗；channel token 失效（401）需提示重新發行。
- **免費額度**：LINE Official Account 免費方案每月有訊息則數上限（依當前方案，數百則/月等級）；
  一天一則推播遠在免費額度內，TCO ≈ 0。

---

## 7. 機密與設定存放（低 TCO 決策）

| 機密 | 存放 | 理由 |
|------|------|------|
| Channel access token（long-lived） | **SSM Parameter Store SecureString** | 免費（Standard tier）、沿用既有 SSM 慣例（`/wendy-tw-stock-bot/dev/lambda_image_tag`）、KMS 預設金鑰加密 |
| Channel secret（Phase 2 簽章驗證用） | **SSM SecureString** | 同上 |
| 推播目標 target_id（userId/groupId） | SSM Parameter（一般 String 即可，非機密但統一管理） | 變更免改碼/重部署 |

- 參數路徑慣例：`/wendy-tw-stock-bot/dev/line/channel_access_token`、`.../line/channel_secret`、`.../line/push_target`。
- **決策：用 SSM SecureString 而非 Secrets Manager**——Secrets Manager $0.40/secret/月 + API 呼叫費；
  SSM SecureString Standard 免費，本專案機密量小、無自動輪替需求，SSM 完全夠用且最省（符合 CLAUDE.md 低 TCO）。
- **本機/手動設定**（一次性，由使用者在 console 或 CLI 做）：
  `aws ssm put-parameter --type SecureString --name /wendy-tw-stock-bot/dev/line/channel_access_token --value '<token>'`
  （⚠️ 本機 bash 雷：名稱開頭 `/` 需 `export MSYS_NO_PATHCONV=1`，沿用既有 SSM 操作注意事項。）
- **絕不寫明文進 repo / env 預設值 / Terraform 變數預設**（呼應 feedback：API key 不入版控）。
  Terraform 只用 `data.aws_ssm_parameter` 在執行期讀，或讓 Lambda 執行時自行 `ssm:GetParameter`（建議後者：
  token 不經過 Terraform state，更安全）。

---

## 8. Terraform Wiring（重用既有 module）

### Phase 1（notifier）
- **ECR**：`module.ecr.repositories` 加 `notifier = {}`（第 4 個 repo）。
- **Lambda**：新增 `module.notifier`（重用 `infra/modules/lambda`，比照 analyzer 寫法）：
  - `count = var.lambda_image_tag != "" ? 1 : 0`、`image_uri = ...ecr...["notifier"]:${tag}`。
  - timeout ~30s（單次 GetItem + 一次 HTTP 呼叫，很快）、memory 256MB（無 pandas，最省）。
  - env：`HOT_TABLE`、`MARKET_HOLIDAYS`、`SSM_PREFIX`（如 `/wendy-tw-stock-bot/dev/line`）。
  - **IAM**（§9）：`dynamodb:GetItem`、`ssm:GetParameter(s)`、`kms:Decrypt`（SecureString 解密）。
- **排程**：新增 `module.schedule_notify`（重用 `eventbridge-schedule`），target notifier，cron 16:30 平日。
- **deploy-images.yml**：build matrix 加 `notifier`（dispatcher/worker/analyzer 已有，仿照加第 4 個 image）。
- **總開關**：沿用 `lambda_image_tag`，notifier/schedule_notify 跟著活化/休眠。

### Phase 2（webhook，選配）— 需先擴充 lambda module
- **lambda module 擴充**：目前 module 只支援 SQS event source，**無 Function URL**。需新增可選參數：
  - `create_function_url`（bool，預設 false）、`function_url_auth_type`（預設 `NONE`）、
    `function_url_cors`（可選）。資源 `aws_lambda_function_url` + 對應 `aws_lambda_permission`
    （`lambda:InvokeFunctionUrl`，principal `*`，`function_url_auth_type = NONE`）。
  - output 新增 `function_url`。
- **webhook Lambda**：`module.webhook`，`create_function_url=true`，env 加 `SSM_PREFIX`、`HOT_TABLE`。
  - IAM：`dynamodb:GetItem`（讀 SUMMARY）、`dynamodb:Query`（讀 GSI2 訊號）、`ssm:GetParameter`、`kms:Decrypt`。
- **ECR**：加 `webhook = {}`（或與 notifier 共用一個 image、用不同 handler——建議分開 repo，職責清晰）。
- **deploy-images.yml**：build matrix 加 `webhook`。
- **設定 LINE webhook URL**：取得 `module.webhook.function_url` 後，到 LINE Developers console
  Messaging API 設定頁填入並啟用 webhook（一次性手動）。

---

## 9. IAM 最小權限

### notifier 執行角色（Phase 1）
- `dynamodb:GetItem`（讀 SUMMARY 單筆）→ `${hot_store.table_arn}`
- `ssm:GetParameter` / `ssm:GetParameters`（讀 token/target）→ `arn:aws:ssm:{region}:{account}:parameter/wendy-tw-stock-bot/dev/line/*`
- `kms:Decrypt`（SecureString 用 AWS 受管金鑰 `alias/aws/ssm` 解密）→ 該 KMS key（或限定 `kms:ViaService = ssm`）
- CloudWatch Logs 由 lambda module 自動建立。

### webhook 執行角色（Phase 2）
- 上述 SSM/KMS 權限（讀 token + secret）
- `dynamodb:GetItem`（SUMMARY）+ `dynamodb:Query`（`${table_arn}/index/GSI2` 讀訊號）

---

## 10. 監控告警（與 Week 6 共用，建議併入）

- notifier 回傳 `{pushed: bool, trade_date, target}`；webhook 記錄處理事件數。
- **CloudWatch Alarm**：
  - notifier Lambda `Errors >= 1`（推播失敗一次就通知——使用者收不到訊息屬高優先）。
  - 可選：`no_summary` 視為上游 analyzer 異常的旁證（與 Week 6 analyzer 告警呼應）。
- 通知管道：SNS → Email（沿用 Week 6 規劃；**注意避免循環**——LINE 推播失敗時不要再用 LINE 告警）。

---

## 11. 成本估算與 TCO

- **LINE Messaging API**：一天一則推播 → 月 ~20 則，遠在免費額度內 → **$0**。
- **Lambda（notifier）**：一天一次、ARM64、數秒、256MB → 幾乎全在免費額度 → **≈ $0**。
- **SSM Parameter Store**：Standard tier SecureString **免費**（vs Secrets Manager $0.40/secret/月）。
- **DynamoDB**：每日 1 次 GetItem（按需）→ 月成本以分計。
- **EventBridge / CloudWatch / SNS Email**：免費額度內。
- **Lambda Function URL（Phase 2）**：無額外固定費（vs API Gateway HTTP API $1/百萬請求 + 仍需設定）；
  webhook 請求量極低 → **≈ $0**。
- **結論**：Week 7-8 新增月成本 **≈ $0**，符合低 TCO；唯一一次性工是 LINE OA 申請與 token 設定。

---

## 12. Work Breakdown（接手 checklist，依序）

> 凡 src/ 改動 push 後由 deploy-images 自動 build/push/部署；純 infra 改動由 terraform.yml 套用。

### Phase 1（主線）
1. **[前置·使用者]** 建立 LINE Official Account + Messaging API channel（LINE Developers console）：
   - 取得 **Channel access token (long-lived)** 與 **Channel secret**。
   - 加自己為好友、取得個人 **userId**（可暫用 LINE 官方 webhook 測試工具 / Phase 2 follow 事件擷取；
     或先用 Messaging API 的「broadcast」對全好友推播，省去取 userId——但 broadcast 無法指定對象）。
2. **[前置·使用者]** 把 3 個值寫進 SSM SecureString（§7 路徑；`MSYS_NO_PATHCONV=1`）。
3. **[infra]** `main.tf`：ecr 加 `notifier`、新增 `module.notifier` + `module.schedule_notify`；
   新增變數 `line_ssm_prefix`（預設 `/wendy-tw-stock-bot/dev/line`）。
   先 `-var lambda_image_tag=<目前tag>` plan 確認預期資源（notifier image 前 count=0 行為）。
4. **[src]** `src/notifier/app.py`：實作 §2 Phase 1 流程
   （交易日防呆、GetItem SUMMARY、組訊息含 facts 標題列、SSM 讀 token/target、urllib Push、回傳統計）。僅 stdlib + boto3。
5. **[src]** `src/notifier/Dockerfile`（base `public.ecr.aws/lambda/python:3.12`，ARM64；無 pandas）。
6. **[ci]** `deploy-images.yml` build matrix 加 `notifier`。
7. **[infra/監控]** notifier CloudWatch Alarm（Errors）+ 接 Week 6 SNS topic（§10）。
8. **[驗證]** push → CI 全綠（`scripts/watch-ci.sh`）→ 手動 invoke notifier（`{"force":true,"trade_date":"<近期交易日>"}`）
   → 確認 LINE 收到推播、log 無 error；再驗 `no_summary` 降級（給沒有摘要的日期）。
9. **[文件/記憶]** 更新 `project_tw_stock_bot_progress.md`（新 tag、新資源、Week 7-8 Phase 1 完成）。

### Phase 2（選配，互動 webhook）
10. **[infra·module]** 擴充 `infra/modules/lambda`：加 `create_function_url` / `function_url_auth_type` /
    `aws_lambda_function_url` + `aws_lambda_permission` + output `function_url`。
11. **[infra]** ecr 加 `webhook`、新增 `module.webhook`（`create_function_url=true`）。
12. **[src]** `src/webhook/app.py`：驗簽（HMAC-SHA256，stdlib `hmac/hashlib/base64`）、解析 events、
    指令路由（今日/盤勢/訊號/help）、follow 擷取 userId（log 出來供設定 push_target）、Reply API 回覆。
13. **[src]** `src/webhook/Dockerfile`、`deploy-images.yml` 加 `webhook`。
14. **[設定·使用者]** 取 `module.webhook.function_url` → LINE console 填 webhook URL 並啟用。
15. **[驗證]** LINE 傳「今日」→ 收到當日摘要；傳「訊號」→ 收到 top N；偽造簽章 → 被拒（403）。

---

## 13. 風險與決策點（給使用者拍板）

| 議題 | 建議 | 替代 |
|------|------|------|
| 交付範圍 | **Phase 1 先收閉環（純排程推播）**，Phase 2 視需要再做 | 一次做完 push+webhook（範圍大、上線慢） |
| 推播對象 | 個人 userId（1-on-1，精準） | broadcast 全好友（免取 userId，但無法指定）／群組 groupId |
| 機密存放 | **SSM Parameter Store SecureString（免費，低 TCO）** | Secrets Manager（有輪替但 $0.40/secret/月，本專案用不到） |
| LINE SDK | **不裝 SDK，urllib 直呼 REST（image 輕）** | `line-bot-sdk`（功能多但增相依與冷啟） |
| webhook 入口（Phase 2） | **Lambda Function URL（免 API Gateway 費）** | API Gateway HTTP API（功能多但多一層成本/設定） |
| 訊息格式 | 純文字（MVP，快） | Flex Message 卡片（美觀但複雜，後續迭代） |
| 如何取得自己 userId | Phase 2 follow 事件自動擷取；Phase 1 暫用 LINE 測試工具/broadcast | 手動查 LINE console |

> **設計賣點（面試可講）**：
> - 完整 event-driven 閉環（排程 → SQS fan-out → ETL → Bedrock grounding 摘要 → LINE 推播），
>   全 Serverless、月成本趨近 $0，展現「低 TCO + 高自動化」的 AWS 落地能力。
> - 機密以 SSM SecureString 管理、IAM 最小權限、webhook 簽章驗證——資安與維運成熟度。
> - 對接外部 SaaS（LINE Messaging API）的整合經驗，呼應 ADR-001「跨服務統一管理」的 Terraform 選型理由。
