output "repository_urls" {
  description = "各 repository 的 URL（docker push 目標 + Lambda image_uri 前綴用）"
  value       = { for k, v in aws_ecr_repository.this : k => v.repository_url }
}

output "repository_arns" {
  description = "各 repository 的 ARN"
  value       = { for k, v in aws_ecr_repository.this : k => v.arn }
}

output "repository_names" {
  description = "各 repository 的名稱"
  value       = { for k, v in aws_ecr_repository.this : k => v.name }
}
