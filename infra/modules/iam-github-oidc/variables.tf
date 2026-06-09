variable "role_name" {
  description = "GitHub Actions 假冒用的 IAM role 名稱"
  type        = string
}

variable "subjects" {
  description = <<-EOT
    允許假冒此 role 的 GitHub OIDC sub 條件（StringLike）。
    例：["repo:OWNER/REPO:*"]（整個 repo）或
        ["repo:OWNER/REPO:ref:refs/heads/main"]（僅 main）。
  EOT
  type    = list(string)
}

variable "create_oidc_provider" {
  description = "是否建立 GitHub OIDC provider。同帳號只能有一個，若已存在請設 false 並傳 oidc_provider_arn"
  type        = bool
  default     = true
}

variable "oidc_provider_arn" {
  description = "既有 GitHub OIDC provider ARN（create_oidc_provider=false 時必填）"
  type        = string
  default     = null
}

variable "managed_policy_arns" {
  description = <<-EOT
    附掛到 role 的受管政策 ARN 清單。
    預設 AdministratorAccess（學習階段，與本機 admin user 一致），
    上線前應收斂為最小權限（僅 state backend + 本專案實際使用的服務）。
  EOT
  type    = list(string)
  default = ["arn:aws:iam::aws:policy/AdministratorAccess"]
}

variable "tags" {
  description = "套用到 role 的標籤"
  type        = map(string)
  default     = {}
}
