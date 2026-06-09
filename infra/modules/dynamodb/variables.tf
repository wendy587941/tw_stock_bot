variable "table_name" {
  description = "DynamoDB 表名稱（同帳號同區內唯一即可）"
  type        = string
}

variable "billing_mode" {
  description = "計費模式，預設按需（免容量規劃、零閒置成本）"
  type        = string
  default     = "PAY_PER_REQUEST"
}

variable "hash_key" {
  description = "Partition key 屬性名稱"
  type        = string
}

variable "range_key" {
  description = "Sort key 屬性名稱（單表設計需要）"
  type        = string
  default     = null
}

variable "attributes" {
  description = "所有作為主鍵或 GSI 鍵的屬性定義（非鍵屬性不需宣告）。type: S/N/B"
  type = list(object({
    name = string
    type = string
  }))
}

variable "global_secondary_indexes" {
  description = "GSI 定義清單"
  type = list(object({
    name               = string
    hash_key           = string
    range_key          = optional(string)
    projection_type    = optional(string, "ALL")
    non_key_attributes = optional(list(string))
  }))
  default = []
}

variable "ttl_attribute" {
  description = "TTL 屬性名稱（epoch 秒），讓舊資料自動過期以控 hot store 成本；null 表示停用"
  type        = string
  default     = null
}

variable "point_in_time_recovery" {
  description = "是否開啟 PITR 連續備份（可回溯 35 天，展現維運成熟度）"
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "是否開啟刪除保護（prod 建議 true；dev 預設 false 便於 destroy）"
  type        = bool
  default     = false
}

variable "tags" {
  description = "套用到表的標籤"
  type        = map(string)
  default     = {}
}
