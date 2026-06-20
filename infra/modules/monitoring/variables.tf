variable "project" {
  description = "專案名稱前綴（用於 SNS topic 命名與自訂指標 namespace）"
  type        = string
}

variable "alarm_email" {
  description = <<-EOT
    告警通知收件 Email（SNS email 訂閱）。留空字串 → 不建立訂閱（只建 topic 與 alarm）。
    ⚠️ SNS email 訂閱建立後為 PendingConfirmation，需到信箱點確認連結才會真正生效（一次性）。
    Email 非機密，可直接放 tfvars/變數預設；變更收件人改此值即可，免改碼。
  EOT
  type        = string
  default     = ""
}

variable "lambda_function_names" {
  description = <<-EOT
    要監控 Errors 的 Lambda 函式名稱 map（key=邏輯名供 alarm 命名，value=實際函式名）。
    例：{ dispatcher = "...-dispatcher-dev", worker = "...", analyzer = "...", notifier = "..." }
    傳空 map（Lambda 尚未活化時）→ 不建立任何 Lambda Errors alarm。
  EOT
  type        = map(string)
  default     = {}
}

variable "dlq_name" {
  description = "ingest 佇列的 DLQ 名稱（監控失敗訊息堆積）。留空 → 不建 DLQ alarm。"
  type        = string
  default     = ""
}

variable "analyzer_log_group_name" {
  description = <<-EOT
    analyzer 的 CloudWatch Log Group 名稱，用來掃 `bedrock_failed_fallback` 降級事件。
    留空 → 不建此 metric filter/alarm（Lambda 未活化時）。
  EOT
  type        = string
  default     = ""
}

variable "notifier_log_group_name" {
  description = <<-EOT
    notifier 的 CloudWatch Log Group 名稱，用來掃 `no_summary`（當日無摘要可推）事件。
    留空 → 不建此 metric filter/alarm。
  EOT
  type        = string
  default     = ""
}

variable "alarm_period_seconds" {
  description = "告警評估週期（秒）。預設 300（5 分鐘），單次評估即觸發（評估期數=1）。"
  type        = number
  default     = 300
}

variable "tags" {
  description = "附加標籤（會與 provider default_tags 合併）"
  type        = map(string)
  default     = {}
}
