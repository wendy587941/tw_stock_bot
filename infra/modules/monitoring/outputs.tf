output "sns_topic_arn" {
  description = "告警 SNS topic ARN（可再掛其他訂閱／供 runbook 參照）"
  value       = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  description = "已建立的所有 CloudWatch alarm 名稱清單"
  value = concat(
    [for a in aws_cloudwatch_metric_alarm.lambda_errors : a.alarm_name],
    aws_cloudwatch_metric_alarm.dlq_messages[*].alarm_name,
    aws_cloudwatch_metric_alarm.bedrock_fallback[*].alarm_name,
    aws_cloudwatch_metric_alarm.no_summary[*].alarm_name,
  )
}
