# Week 6 規劃書 — CloudWatch 監控告警（monitoring module）

> 版本：v1（2026-06-20）｜接手對象：下一位實作 AI Agent（本書已隨同實作落地，供回溯與面試說明）
> 前置狀態：ETL（dispatcher/worker）+ analyzer（含 Bedrock 失敗 fallback）+ notifier 皆已上線
> （部署 tag `bac7422`）。Bedrock Marketplace 訂閱 blocker 已於 2026-06-20 解除（根因=帳號付款方式）。

---

## 1. 目標與範圍

讓整條 Serverless pipeline 從「壞了才被使用者發現」進化到「壞了主動寄信通知」。
以**最低 TCO**（全部落在 CloudWatch/SNS 免費額度內）補上維運可觀測性，作為新加坡遠端職位面試的維運成熟度賣點。

**MVP 範圍（本週）：** 三層告警彙整到單一 SNS topic → Email。
**明確不做：** CloudWatch Dashboard、X-Ray 分散式追蹤、PagerDuty/Slack 整合、自訂 SLO/SLA 報表（後續波次）。

---

## 2. 告警三層設計

| 層 | 來源 | 指標 | 觸發條件 | 意義 |
|----|------|------|---------|------|
| L1 基礎設施 | 4 支 Lambda | `AWS/Lambda` Errors | Sum ≥ 1 / 5 分鐘 | 任一 Lambda 拋未捕捉例外 |
| L2 解耦緩衝 | ingest DLQ | `AWS/SQS` ApproximateNumberOfMessagesVisible | Max ≥ 1 | 個股反覆抓取失敗、訊息落 DLQ |
| L3 應用語意 | analyzer log | 自訂 `BedrockFallback` | Sum ≥ 1 | 當日走 fallback 降級（非 AI 版）|
| L3 應用語意 | notifier log | 自訂 `NoSummary` | Sum ≥ 1 | 當日無摘要可推（使用者收不到 LINE）|

- L3 透過 **CloudWatch Logs metric filter** 把 log 關鍵字轉成自訂指標（namespace `<project>/monitoring`）。
  - `bedrock_failed_fallback` ← analyzer handler 降級時的 `WARN` log（Week 5 fallback 一併埋的觀測點）。
  - `no summary in hot store` ← notifier 找不到當日 SUMMARY 的 skip log。
- 所有 alarm `treat_missing_data = notBreaching`：休市/未活化沒資料時不誤報 INSUFFICIENT_DATA。
- `default_value = 0`：log 指標無匹配時補 0，讓指標連續、alarm 狀態穩定。

---

## 3. 匯流與通知

```
4×Lambda Errors ┐
DLQ depth       ├─→ 各 aws_cloudwatch_metric_alarm ─→ SNS topic「<project>-alerts」─→ Email
BedrockFallback ┤                                          ↑
NoSummary       ┘                          alarm_actions + ok_actions（恢復也通知）
```

- 單一 SNS topic 收斂所有 alarm，便於日後加訂閱（Slack/Lambda 自動修復）不必改每個 alarm。
- **避免告警迴圈**：LINE 推播失敗用 Email 告警（不再用 LINE），呼應 notifier 規劃書 §10。
- `ok_actions` 也指向 topic → 問題恢復時收到「OK」信，省去人工確認是否已自癒。

---

## 4. 低 TCO 決策

| 項目 | 決策 | 理由 |
|------|------|------|
| Alarm 數量 | 8 個（4 Lambda + 1 DLQ + 2 應用 + 預留）| CloudWatch 前 10 個 alarm 免費，落在額度內 |
| Log metric filter | 免費 | filter 本身不計費，只在比對既有 log，無額外 ingestion |
| SNS Email | 免費額度內 | 每月前 1,000 封 Email 通知免費，告警量遠低於此 |
| SNS 加密 | **不啟用 SSE** | `aws/sns` 受管金鑰 key policy 不授 cloudwatch.amazonaws.com，會導致 alarm 無法 publish；告警訊息非機密，維持預設不加密（避免踩雷又省 KMS 成本）|
| Email 存放 | tfvars 變數（非 SSM）| Email 非機密；若走 SSM data source 會在 plan 期要求參數存在、反而卡 CI |

**新增月成本 ≈ $0。**

---

## 5. 模組與 Wiring

- **新增 `infra/modules/monitoring/`**（可重用）：
  - 輸入：`project`、`alarm_email`、`lambda_function_names`(map)、`dlq_name`、
    `analyzer_log_group_name`、`notifier_log_group_name`、`alarm_period_seconds`、`tags`。
  - 資源：SNS topic + email 訂閱（count 守門）、Lambda Errors alarm（for_each map）、
    DLQ alarm（count）、2 組 log metric filter + alarm（count 守門）。
  - 輸出：`sns_topic_arn`、`alarm_names`。
- **`infra/modules/lambda/outputs.tf`**：新增 `log_group_name` 輸出 → 讓 metric filter 正確依賴 log group。
- **`infra/environments/dev/`**：
  - `variables.tf`：新增 `alarm_email`（預設使用者 Email）。
  - `main.tf`：新增 `module.monitoring`。Lambda 相關輸入用 `length(module.X) > 0 ? ... : ""/{}` 守門
    → 未活化時不建 Lambda/log alarm；SNS topic 與 DLQ alarm 因不依賴 Lambda 故恆建立。
  - `outputs.tf`：新增 `alerts_sns_topic_arn`、`alarm_names`。

---

## 6. 部署與驗證

> 純 infra 改動 → 由 `terraform.yml`（push main）套用；`deploy-images.yml` 不觸發（未動 src/）。

1. **[plan]** `terraform plan -var lambda_image_tag=<目前tag>` → 預期 **11 to add / 0 change / 0 destroy**。
2. **[apply]** push main → terraform CI 從 SSM 讀 tag → apply 建 11 資源。
3. **[一次性·使用者]** 收 AWS SNS 確認信 → 點連結確認訂閱（否則收不到告警）。
4. **[驗證·正向]** invoke analyzer 對「有資料但故意觸發降級」或查 alarm state 由 OK→ALARM。
   - 簡易驗證：`aws cloudwatch describe-alarms` 看 8 個 alarm 皆 OK（無資料時 notBreaching）。
   - 真實驗證：DLQ 灌一筆測試訊息 → 5 分鐘內收到 DLQ alarm Email → 清掉 → 收 OK 信。
5. **[文件/記憶]** 更新 `project_tw_stock_bot_progress.md`（Week 6 完成、SNS topic ARN、待使用者確認訂閱）。

---

## 7. 後續迭代（非本週）

- CloudWatch Dashboard（單頁看 pipeline 健康度，面試 demo 效果佳）。
- `degraded` 連續 N 天才告警（避免單日 Bedrock 抖動誤報）→ 改 evaluation_periods 或 anomaly detection。
- X-Ray 追蹤 dispatcher→SQS→worker→analyzer 端到端延遲。
- 成本告警（AWS Budgets）月帳單超 $60 通知，呼應 README 低 TCO 原則。
