output "function_arn" {
  description = "Lambda 函式 ARN（供 EventBridge 排程等觸發來源指定目標）"
  value       = aws_lambda_function.this.arn
}

output "function_name" {
  description = "Lambda 函式名稱"
  value       = aws_lambda_function.this.function_name
}

output "role_name" {
  description = "執行角色名稱（供外部再附掛政策用）"
  value       = aws_iam_role.this.name
}

output "role_arn" {
  description = "執行角色 ARN"
  value       = aws_iam_role.this.arn
}
