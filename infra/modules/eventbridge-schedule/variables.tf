variable "schedule_name" {
  description = "排程名稱（同帳號同區唯一）"
  type        = string
}

variable "description" {
  description = "排程用途說明"
  type        = string
  default     = null
}

variable "cron_expression" {
  description = "排程運算式。EventBridge Scheduler 支援 cron()/rate()/at()，cron 格式為 cron(分 時 日 月 週 年)"
  type        = string
}

variable "timezone" {
  description = "排程時區。Scheduler 原生支援時區（不像舊 EventBridge Rules 只能 UTC），台股場景用 Asia/Taipei 免手算時差"
  type        = string
  default     = "Asia/Taipei"
}

variable "flexible_time_window_minutes" {
  description = "彈性時間窗（分鐘）。0 表示精準觸發（OFF）；>0 則 AWS 可在此窗內擇時觸發以分散負載"
  type        = number
  default     = 0
}

variable "state" {
  description = "排程狀態 ENABLED / DISABLED。下游尚未接好時可先設 DISABLED"
  type        = string
  default     = "ENABLED"
}

variable "target_arn" {
  description = "觸發目標 ARN（如 dispatcher Lambda）"
  type        = string
}

variable "target_input" {
  description = "傳給目標的 JSON 輸入字串（如 {\"job\":\"daily-etl\"}），null 表示不帶 payload"
  type        = string
  default     = null
}

variable "target_action" {
  description = "授予 scheduler 執行角色對目標的權限 action。Lambda 目標用 lambda:InvokeFunction；SQS 用 sqs:SendMessage"
  type        = string
  default     = "lambda:InvokeFunction"
}

variable "maximum_retry_attempts" {
  description = "觸發失敗最大重試次數"
  type        = number
  default     = 2
}

variable "maximum_event_age_seconds" {
  description = "事件最大存活秒數，超過即放棄重試（預設 1 天）"
  type        = number
  default     = 86400
}

variable "dead_letter_arn" {
  description = "觸發失敗後投遞的 SQS DLQ ARN，null 表示不設（生產環境建議設定以免靜默失敗）"
  type        = string
  default     = null
}

variable "tags" {
  description = "套用到排程與執行角色的標籤"
  type        = map(string)
  default     = {}
}
