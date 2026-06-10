output "queue_url" {
  description = "主佇列 URL（dispatcher 送訊 send_message 用）"
  value       = aws_sqs_queue.this.id
}

output "queue_arn" {
  description = "主佇列 ARN（worker Lambda event source mapping 與 IAM policy 授權用）"
  value       = aws_sqs_queue.this.arn
}

output "queue_name" {
  description = "主佇列實際名稱（含 FIFO 後綴）"
  value       = aws_sqs_queue.this.name
}

output "dlq_url" {
  description = "DLQ URL（重放或清理失敗訊息用）"
  value       = aws_sqs_queue.dlq.id
}

output "dlq_arn" {
  description = "DLQ ARN（供 Week 6 監控告警掛 CloudWatch alarm 用）"
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "DLQ 實際名稱"
  value       = aws_sqs_queue.dlq.name
}
