locals {
  # FIFO 佇列名稱必須以 .fifo 結尾；標準佇列則維持原名
  suffix     = var.fifo_queue ? ".fifo" : ""
  queue_name = "${var.queue_name}${local.suffix}"
  dlq_name   = "${var.queue_name}-dlq${local.suffix}"
}

# 死信佇列（DLQ）：收容重試多次仍失敗的毒訊息，與主流程隔離以利事後調查
resource "aws_sqs_queue" "dlq" {
  name       = local.dlq_name
  fifo_queue = var.fifo_queue

  message_retention_seconds = var.dlq_message_retention_seconds

  # SQS 受管伺服器端加密（免費、免維護 KMS 金鑰），符合低 TCO 原則
  sqs_managed_sse_enabled = true

  tags = var.tags
}

# 主佇列：dispatcher 派工、worker 消費，fan-out 解耦緩衝
resource "aws_sqs_queue" "this" {
  name       = local.queue_name
  fifo_queue = var.fifo_queue

  # 內容去重僅在 FIFO 下有意義
  content_based_deduplication = var.fifo_queue ? var.content_based_deduplication : null

  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.message_retention_seconds
  receive_wait_time_seconds  = var.receive_wait_time_seconds
  max_message_size           = var.max_message_size
  delay_seconds              = var.delay_seconds

  sqs_managed_sse_enabled = true

  # 失敗達 max_receive_count 次即轉送 DLQ
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = var.tags
}

# 限制：此 DLQ 只接受來自上面主佇列的 redrive，避免被其他佇列誤用為 DLQ
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.this.arn]
  })
}
