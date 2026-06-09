provider "aws" {
  region = var.region

  # 所有資源自動帶上的共用標籤，便於成本歸戶與管理
  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}
