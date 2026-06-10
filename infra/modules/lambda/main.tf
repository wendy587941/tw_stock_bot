locals {
  # worker 消費佇列所需的 SQS 權限（僅在啟用 event source 時加入）
  sqs_statements = var.create_sqs_event_source ? [{
    sid       = "SQSConsume"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [var.sqs_queue_arn]
  }] : []

  all_statements = concat(var.additional_iam_statements, local.sqs_statements)
}

# 先建 log group 並設保留天數（不讓 Lambda 自動建立的群組變成永久保留）
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# 執行角色：信任 Lambda 服務
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

# 基礎執行權限（寫 CloudWatch Logs）
resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 業務最小權限（S3 / DynamoDB / SQS 等）+ 消費佇列權限，合併成單一 inline policy
data "aws_iam_policy_document" "extra" {
  count = length(local.all_statements) > 0 ? 1 : 0

  dynamic "statement" {
    for_each = local.all_statements
    content {
      sid       = statement.value.sid
      effect    = "Allow"
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role_policy" "extra" {
  count  = length(local.all_statements) > 0 ? 1 : 0
  name   = "app-permissions"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.extra[0].json
}

resource "aws_lambda_function" "this" {
  function_name = var.function_name
  description   = var.description
  role          = aws_iam_role.this.arn

  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = var.architectures
  memory_size   = var.memory_size
  timeout       = var.timeout

  reserved_concurrent_executions = var.reserved_concurrent_executions

  dynamic "environment" {
    for_each = length(var.environment_variables) > 0 ? [var.environment_variables] : []
    content {
      variables = environment.value
    }
  }

  tags = var.tags

  # 確保 log group 先建好（避免 Lambda 首次執行自動建立成永久保留群組）
  depends_on = [aws_cloudwatch_log_group.this]
}

# SQS → Lambda 觸發（worker）。回報部分批次失敗，讓成功訊息不被整批重投
resource "aws_lambda_event_source_mapping" "sqs" {
  count = var.create_sqs_event_source ? 1 : 0

  event_source_arn                   = var.sqs_queue_arn
  function_name                      = aws_lambda_function.this.arn
  batch_size                         = var.sqs_batch_size
  maximum_batching_window_in_seconds = var.sqs_max_batching_window_seconds
  function_response_types            = ["ReportBatchItemFailures"]
}
