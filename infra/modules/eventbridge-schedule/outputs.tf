output "schedule_arn" {
  description = "EventBridge Scheduler 排程 ARN"
  value       = aws_scheduler_schedule.this.arn
}

output "schedule_name" {
  description = "排程名稱"
  value       = aws_scheduler_schedule.this.name
}

output "scheduler_role_arn" {
  description = "Scheduler 觸發目標時假冒的執行角色 ARN"
  value       = aws_iam_role.scheduler.arn
}
