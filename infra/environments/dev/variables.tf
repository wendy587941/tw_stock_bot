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
