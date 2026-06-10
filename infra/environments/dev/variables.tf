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

variable "lambda_image_tag" {
  description = <<-EOT
    dispatcher / worker 容器 image 的 tag（如 "latest" 或 git sha）。
    這是 Lambda 與排程的總開關：
      - 留空字串 → dispatcher/worker/schedule 全 count=0 不建立
        （容器 Lambda 必須先有 image 才能建，故未 build/push 前保持休眠）
      - 填入已 push 到 ECR 的 tag → 整條 排程→dispatcher→SQS→worker 一次活化
    image_uri 由模組自動組成：<ecr_repo_url>:<lambda_image_tag>
  EOT
  type        = string
  default     = ""
}
