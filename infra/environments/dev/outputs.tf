output "data_lake_bucket_ids" {
  description = "資料湖各 layer 的 bucket 名稱"
  value       = module.data_lake.bucket_ids
}

output "data_lake_bucket_arns" {
  description = "資料湖各 layer 的 bucket ARN"
  value       = module.data_lake.bucket_arns
}

output "hot_store_table_name" {
  description = "DynamoDB hot store 表名稱"
  value       = module.hot_store.table_name
}

output "hot_store_table_arn" {
  description = "DynamoDB hot store 表 ARN"
  value       = module.hot_store.table_arn
}

output "github_actions_role_arn" {
  description = "GitHub Actions workflow 要填的 role-to-assume ARN"
  value       = module.github_oidc.role_arn
}
