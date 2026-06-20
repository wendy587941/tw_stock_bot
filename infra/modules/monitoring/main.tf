# 監控告警 module — 三類來源彙整到單一 SNS topic → Email。
# 設計：低 TCO（CloudWatch alarm 前 10 個免費、log metric filter 免費、SNS Email 免費額度內）、
#       低維運（全 Terraform 宣告，無常駐元件）。各 alarm 來源缺資料一律 treat_missing_data=notBreaching，
#       避免休市/未活化時誤報 INSUFFICIENT_DATA。

locals {
  # 自訂指標 namespace（log metric filter 用），與 AWS/Lambda、AWS/SQS 等內建 namespace 區隔
  metric_namespace = "${var.project}/monitoring"
}

# ── 告警匯流：單一 SNS topic + Email 訂閱 ────────────────────────────────────
# 決策：SNS 不啟用 SSE。aws/sns 受管金鑰的 key policy 不授 cloudwatch.amazonaws.com，
#   會導致 alarm 無法 publish 到加密 topic；告警訊息本身非機密，故維持預設不加密（避免踩雷又省成本）。
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ── 第一層：每支 Lambda 的 Errors（任一次失敗就通知）───────────────────────
# Errors>=1：dispatcher/worker/analyzer/notifier 任一拋未捕捉例外（含 worker 部分批次失敗回報）即告警。
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = var.lambda_function_names

  alarm_name        = "${each.value}-errors"
  alarm_description = "${each.key} Lambda 發生執行錯誤（Errors>=1），可能影響當日 pipeline。"

  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = var.tags
}

# ── 第二層：SQS DLQ 堆積（worker 重試 5 次仍失敗的訊息落 DLQ）────────────────
# DLQ 出現任何可見訊息 → 代表有個股反覆抓取失敗，需人工調查/重放。
resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  count = var.dlq_name != "" ? 1 : 0

  alarm_name        = "${var.dlq_name}-messages"
  alarm_description = "ingest DLQ 出現失敗訊息（worker 重試耗盡），需調查或重放。"

  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = var.tags
}

# ── 第三層：應用層語意事件（log metric filter → 自訂指標 → alarm）────────────
# 3a. analyzer Bedrock 降級：handler 失敗時 log `WARN bedrock_failed_fallback ...`（degraded 摘要）。
#     觸發代表當日是 fallback 純數據版而非 AI 版 → 提示去查 Bedrock（訂閱失效/限流/服務異常）。
resource "aws_cloudwatch_log_metric_filter" "bedrock_fallback" {
  count = var.analyzer_log_group_name != "" ? 1 : 0

  name           = "${var.project}-bedrock-fallback"
  log_group_name = var.analyzer_log_group_name
  pattern        = "\"bedrock_failed_fallback\""

  metric_transformation {
    name          = "BedrockFallback"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0" # 無匹配時補 0 → 指標連續，alarm 不卡 INSUFFICIENT_DATA
  }
}

resource "aws_cloudwatch_metric_alarm" "bedrock_fallback" {
  count = var.analyzer_log_group_name != "" ? 1 : 0

  alarm_name        = "${var.project}-bedrock-fallback"
  alarm_description = "analyzer 走 Bedrock 失敗降級（fallback 純數據摘要）。請檢查 Bedrock 模型存取/訂閱/限流。"

  namespace           = local.metric_namespace
  metric_name         = "BedrockFallback"
  statistic           = "Sum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = var.tags
}

# 3b. notifier 無摘要可推：當日 analyzer 未產出摘要時 notifier log `skip: no summary in hot store ...`。
#     觸發代表使用者當天收不到 LINE 推播 → 上游 analyzer 異常的旁證。
resource "aws_cloudwatch_log_metric_filter" "no_summary" {
  count = var.notifier_log_group_name != "" ? 1 : 0

  name           = "${var.project}-no-summary"
  log_group_name = var.notifier_log_group_name
  pattern        = "\"no summary in hot store\""

  metric_transformation {
    name          = "NoSummary"
    namespace     = local.metric_namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "no_summary" {
  count = var.notifier_log_group_name != "" ? 1 : 0

  alarm_name        = "${var.project}-no-summary"
  alarm_description = "notifier 當日無摘要可推（使用者收不到 LINE）。請檢查 analyzer 是否成功產出當日 SUMMARY。"

  namespace           = local.metric_namespace
  metric_name         = "NoSummary"
  statistic           = "Sum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = var.tags
}
