output "role_arn" {
  description = "GitHub Actions workflow 用 aws-actions/configure-aws-credentials 的 role-to-assume"
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "IAM role 名稱"
  value       = aws_iam_role.this.name
}

output "oidc_provider_arn" {
  description = "GitHub OIDC provider ARN"
  value       = local.provider_arn
}
