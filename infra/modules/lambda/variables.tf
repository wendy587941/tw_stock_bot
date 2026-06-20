variable "function_name" {
  description = "Lambda 函式名稱"
  type        = string
}

variable "image_uri" {
  description = "ECR 容器 image URI（含 tag 或 digest），容器型 Lambda 部署來源"
  type        = string
}

variable "description" {
  description = "函式用途說明"
  type        = string
  default     = null
}

variable "architectures" {
  description = "CPU 架構。預設 arm64（Graviton2），同效能下比 x86 便宜約 20%，符合低 TCO"
  type        = list(string)
  default     = ["arm64"]
}

variable "memory_size" {
  description = "記憶體 MB（同時等比例決定 CPU 配額）"
  type        = number
  default     = 512
}

variable "timeout" {
  description = "逾時秒數。需 < 觸發來源 SQS 的 visibility timeout（本專案 ingest queue 為 360s）"
  type        = number
  default     = 60
}

variable "environment_variables" {
  description = "注入函式的環境變數（如 SQS_QUEUE_URL、目標 bucket、DynamoDB 表名）"
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch Logs 保留天數，避免日誌無限累積成本"
  type        = number
  default     = 14
}

variable "additional_iam_statements" {
  description = <<-EOT
    附加到執行角色的 IAM 權限陳述（最小權限）。例：授 S3 PutObject、DynamoDB PutItem、
    SQS SendMessage 等。基礎的 CloudWatch Logs 權限模組已自動附掛，不需在此重複。
  EOT
  type = list(object({
    sid       = optional(string)
    actions   = list(string)
    resources = list(string)
  }))
  default = []
}

variable "create_sqs_event_source" {
  description = "是否建立 SQS event source mapping（worker 消費佇列用）"
  type        = bool
  default     = false
}

variable "sqs_queue_arn" {
  description = "觸發來源 SQS 佇列 ARN（create_sqs_event_source=true 時必填）"
  type        = string
  default     = null
}

variable "sqs_batch_size" {
  description = "單次傳給函式的最大訊息數"
  type        = number
  default     = 10
}

variable "sqs_max_batching_window_seconds" {
  description = "湊批等待秒數，0 表示有訊息即觸發"
  type        = number
  default     = 0
}

variable "reserved_concurrent_executions" {
  description = "保留併發數，-1 表示不設限（與帳號共用池）"
  type        = number
  default     = -1
}

variable "create_function_url" {
  description = "是否建立 Lambda Function URL（HTTP 觸發、免 API Gateway；webhook 用）"
  type        = bool
  default     = false
}

variable "function_url_auth_type" {
  description = <<-EOT
    Function URL 認證方式。
    NONE = 公開端點（需應用層自行驗章，如 LINE webhook 的 X-Line-Signature HMAC）；
    AWS_IAM = 呼叫端需 SigV4 簽章。預設 NONE 供外部 SaaS（LINE）webhook 直接呼叫。
  EOT
  type        = string
  default     = "NONE"
}

variable "tags" {
  description = "套用到函式與相關資源的標籤"
  type        = map(string)
  default     = {}
}
