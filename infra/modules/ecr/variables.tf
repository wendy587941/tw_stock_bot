variable "project" {
  description = "專案前綴，用於組成 repository 名稱"
  type        = string
}

variable "repositories" {
  description = <<-EOT
    要建立的 ECR repository 集合。key 為服務名（dispatcher/worker），
    repository 實際名稱為 "<project>-<key>"。
  EOT
  type = map(object({
    # 標籤可變性：dev 用 MUTABLE 方便覆蓋 :latest；prod 建議 IMMUTABLE 確保可追溯
    image_tag_mutability = optional(string, "MUTABLE")
    # push 時自動弱點掃描（免費），符合安全最佳實務
    scan_on_push = optional(bool, true)
    # 只保留最近 N 個有標籤 image，舊的自動清除以控儲存成本
    keep_last_images = optional(number, 10)
    # dev 允許連同 image 一起刪除 repo；prod 應設 false
    force_delete = optional(bool, true)
  }))
}

variable "tags" {
  description = "套用到所有 repository 的共用標籤"
  type        = map(string)
  default     = {}
}
