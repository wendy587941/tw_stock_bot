output "table_name" {
  description = "DynamoDB 表名稱"
  value       = aws_dynamodb_table.this.name
}

output "table_arn" {
  description = "DynamoDB 表 ARN（供 Lambda IAM policy 授權用）"
  value       = aws_dynamodb_table.this.arn
}

output "table_stream_arn" {
  description = "DynamoDB Stream ARN（若未開 stream 則為空字串）"
  value       = aws_dynamodb_table.this.stream_arn
}
