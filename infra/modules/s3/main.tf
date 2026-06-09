locals {
  # layer key -> 實際 bucket 名稱
  bucket_names = { for k, v in var.buckets : k => "${var.project}-${k}-${var.region}" }
}

resource "aws_s3_bucket" "this" {
  for_each = var.buckets
  bucket   = local.bucket_names[each.key]

  tags = merge(var.tags, {
    Layer = each.key
  })
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  versioning_configuration {
    status = each.value.enable_versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id

  block_public_acls       = true
  block_public_policy      = true
  ignore_public_acls       = true
  restrict_public_buckets = true
}

# 僅在有定義 transitions / expiration / 舊版過期時才建立 lifecycle 設定
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = {
    for k, v in var.buckets : k => v
    if length(v.transitions) > 0 || v.expiration_days != null || v.noncurrent_version_expiration_days != null
  }
  bucket = aws_s3_bucket.this[each.key].id

  rule {
    id     = "medallion-lifecycle"
    status = "Enabled"

    filter {} # 套用至整個 bucket

    dynamic "transition" {
      for_each = each.value.transitions
      content {
        days          = transition.value.days
        storage_class = transition.value.storage_class
      }
    }

    dynamic "expiration" {
      for_each = each.value.expiration_days != null ? [each.value.expiration_days] : []
      content {
        days = expiration.value
      }
    }

    dynamic "noncurrent_version_expiration" {
      for_each = each.value.noncurrent_version_expiration_days != null ? [each.value.noncurrent_version_expiration_days] : []
      content {
        noncurrent_days = noncurrent_version_expiration.value
      }
    }
  }

  # lifecycle 需 versioning 先就緒
  depends_on = [aws_s3_bucket_versioning.this]
}
