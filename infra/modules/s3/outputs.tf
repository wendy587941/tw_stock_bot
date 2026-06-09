output "bucket_ids" {
  description = "layer -> bucket 名稱"
  value       = { for k, b in aws_s3_bucket.this : k => b.id }
}

output "bucket_arns" {
  description = "layer -> bucket ARN"
  value       = { for k, b in aws_s3_bucket.this : k => b.arn }
}
