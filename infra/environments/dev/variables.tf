variable "project" {
  description = "專案名稱前綴"
  type        = string
  default     = "wendy-tw-stock-bot"
}

variable "environment" {
  description = "部署環境（dev/prod）"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-1"
}

variable "dispatcher_lambda_arn" {
  description = <<-EOT
    ETL dispatcher Lambda 的 ARN，供 EventBridge 排程當觸發目標。
    Week 3-4 建立 Lambda 後填入（或改為引用 module.dispatcher.arn）。
    目前留空字串 → 排程 module count=0 暫不建立，避免指向不存在的目標。
  EOT
  type        = string
  default     = ""
}
