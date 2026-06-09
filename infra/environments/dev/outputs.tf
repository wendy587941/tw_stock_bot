output "data_lake_bucket_ids" {
  description = "資料湖各 layer 的 bucket 名稱"
  value       = module.data_lake.bucket_ids
}

output "data_lake_bucket_arns" {
  description = "資料湖各 layer 的 bucket ARN"
  value       = module.data_lake.bucket_arns
}
