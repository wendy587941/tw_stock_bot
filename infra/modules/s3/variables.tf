variable "project" {
  description = "專案前綴，用於組成 bucket 名稱（需全球唯一）"
  type        = string
}

variable "region" {
  description = "AWS region，併入 bucket 名稱避免跨區衝突"
  type        = string
}

variable "buckets" {
  description = <<-EOT
    要建立的資料湖 bucket 集合。key 為 layer 名稱（raw/curated/marts），
    bucket 實際名稱為 "<project>-<key>-<region>"。
  EOT
  type = map(object({
    enable_versioning = optional(bool, true)
    # 生命週期轉儲規則（依 Medallion 由熱到冷），days 為物件建立後天數
    transitions = optional(list(object({
      days          = number
      storage_class = string
    })), [])
    # 物件過期刪除天數，null 表示永久保留
    expiration_days = optional(number, null)
    # 非當前版本（舊版）保留天數，避免 versioning 無限累積成本
    noncurrent_version_expiration_days = optional(number, 90)
  }))
}

variable "tags" {
  description = "套用到所有 bucket 的共用標籤"
  type        = map(string)
  default     = {}
}
