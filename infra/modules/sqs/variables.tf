variable "queue_name" {
  description = "主佇列基礎名稱（FIFO 時模組會自動補上 .fifo 後綴；DLQ 為此名稱加 -dlq）"
  type        = string
}

variable "fifo_queue" {
  description = "是否建立 FIFO 佇列（嚴格順序 + 去重）。fan-out 派工不需順序，預設標準佇列即可"
  type        = bool
  default     = false
}

variable "content_based_deduplication" {
  description = "FIFO 專用：以訊息內容雜湊自動去重（免在送訊時帶 dedup id）。僅 fifo_queue=true 時生效"
  type        = bool
  default     = false
}

variable "visibility_timeout_seconds" {
  description = "訊息被取出後的隱藏時間。最佳實務設為消費端 Lambda timeout 的 6 倍，避免處理中被重複取出"
  type        = number
  default     = 360
}

variable "message_retention_seconds" {
  description = "主佇列訊息保留秒數，超過即丟棄。預設 4 天（AWS 預設值），日批場景足夠"
  type        = number
  default     = 345600
}

variable "receive_wait_time_seconds" {
  description = "Long polling 等待秒數。設 20（最大值）可大幅減少空輪詢次數 → 省請求成本與 CPU"
  type        = number
  default     = 20
}

variable "max_message_size" {
  description = "單則訊息最大位元組。預設 256KB（AWS 上限）；派工訊息通常僅含股票代號等少量欄位"
  type        = number
  default     = 262144
}

variable "delay_seconds" {
  description = "訊息送入後延遲多久才可被取出。預設 0（立即可取）"
  type        = number
  default     = 0
}

variable "max_receive_count" {
  description = "訊息被取出處理失敗達此次數後，自動轉送 DLQ（毒訊息隔離），避免無限重試卡住佇列"
  type        = number
  default     = 5
}

variable "dlq_message_retention_seconds" {
  description = "DLQ 訊息保留秒數。預設 14 天（AWS 上限），給足時間調查失敗訊息再決定重放或丟棄"
  type        = number
  default     = 1209600
}

variable "tags" {
  description = "套用到佇列與 DLQ 的標籤"
  type        = map(string)
  default     = {}
}
