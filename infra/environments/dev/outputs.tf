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

output "ingest_queue_url" {
  description = "派工主佇列 URL（dispatcher 送訊用）"
  value       = module.ingest_queue.queue_url
}

output "ingest_queue_arn" {
  description = "派工主佇列 ARN（worker event source mapping 用）"
  value       = module.ingest_queue.queue_arn
}

output "ingest_dlq_url" {
  description = "派工 DLQ URL（失敗訊息調查／重放用）"
  value       = module.ingest_queue.dlq_url
}

output "ecr_repository_urls" {
  description = "ECR repository URL（docker build/push 與 Lambda image_uri 用）"
  value       = module.ecr.repository_urls
}

output "dispatcher_function_name" {
  description = "Dispatcher Lambda 名稱（lambda_image_tag 未設時為 null）"
  value       = one(module.dispatcher[*].function_name)
}

output "worker_function_name" {
  description = "Worker Lambda 名稱（lambda_image_tag 未設時為 null）"
  value       = one(module.worker[*].function_name)
}

output "etl_schedule_arn" {
  description = "每日 ETL 排程 ARN（lambda_image_tag 未設時為 null）"
  value       = one(module.schedule_etl[*].schedule_arn)
}

output "github_actions_role_arn" {
  description = "GitHub Actions workflow 要填的 role-to-assume ARN"
  value       = module.github_oidc.role_arn
}

output "alerts_sns_topic_arn" {
  description = "監控告警 SNS topic ARN"
  value       = module.monitoring.sns_topic_arn
}

output "alarm_names" {
  description = "已建立的 CloudWatch alarm 名稱清單"
  value       = module.monitoring.alarm_names
}
