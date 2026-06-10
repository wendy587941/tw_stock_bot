locals {
  # 服務 key -> 實際 repository 名稱
  repo_names = { for k, v in var.repositories : k => "${var.project}-${k}" }
}

resource "aws_ecr_repository" "this" {
  for_each = var.repositories

  name                 = local.repo_names[each.key]
  image_tag_mutability = each.value.image_tag_mutability
  force_delete         = each.value.force_delete

  image_scanning_configuration {
    scan_on_push = each.value.scan_on_push
  }

  # ECR 預設 AES256 靜態加密（免費、免維護 KMS），符合低 TCO
  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(var.tags, {
    Service = each.key
  })
}

# 生命週期政策：只留最近 N 個有標籤 image + 過期未標籤 image，避免儲存費無限累積
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = var.repositories
  repository = aws_ecr_repository.this[each.key].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "保留最近 ${each.value.keep_last_images} 個有標籤 image"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = each.value.keep_last_images
        }
        action = { type = "expire" }
      },
    ]
  })
}
