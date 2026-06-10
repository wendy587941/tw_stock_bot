# EventBridge Scheduler 觸發目標時所假冒的執行角色（信任 scheduler 服務）
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.schedule_name}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

# 僅授予「對指定目標執行 target_action」與「對 DLQ 送訊」的最小權限
data "aws_iam_policy_document" "invoke" {
  statement {
    effect    = "Allow"
    actions   = [var.target_action]
    resources = [var.target_arn]
  }

  dynamic "statement" {
    for_each = var.dead_letter_arn != null ? [var.dead_letter_arn] : []
    content {
      effect    = "Allow"
      actions   = ["sqs:SendMessage"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "invoke" {
  name   = "invoke-target"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.invoke.json
}

resource "aws_scheduler_schedule" "this" {
  name        = var.schedule_name
  description = var.description
  state       = var.state

  schedule_expression          = var.cron_expression
  schedule_expression_timezone = var.timezone

  flexible_time_window {
    mode                      = var.flexible_time_window_minutes > 0 ? "FLEXIBLE" : "OFF"
    maximum_window_in_minutes = var.flexible_time_window_minutes > 0 ? var.flexible_time_window_minutes : null
  }

  target {
    arn      = var.target_arn
    role_arn = aws_iam_role.scheduler.arn
    input    = var.target_input

    retry_policy {
      maximum_retry_attempts       = var.maximum_retry_attempts
      maximum_event_age_in_seconds = var.maximum_event_age_seconds
    }

    # Scheduler 以執行角色觸發，不需在目標 Lambda 上掛 resource policy
    dynamic "dead_letter_config" {
      for_each = var.dead_letter_arn != null ? [var.dead_letter_arn] : []
      content {
        arn = dead_letter_config.value
      }
    }
  }
}
