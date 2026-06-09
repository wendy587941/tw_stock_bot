# 資料湖三 bucket（Medallion / Hot-Warm-Cold 生命週期）
module "data_lake" {
  source  = "../../modules/s3"
  project = var.project
  region  = var.region

  buckets = {
    # 🥉 Bronze：原始 API response（JSON），最不常存取 → 積極轉冷
    raw = {
      transitions = [
        { days = 30, storage_class = "STANDARD_IA" },
        { days = 90, storage_class = "GLACIER" },
        { days = 180, storage_class = "DEEP_ARCHIVE" },
      ]
    }
    # 🥈 Silver：清洗後 Parquet，供 Athena 查詢 → 溫存
    curated = {
      transitions = [
        { days = 90, storage_class = "STANDARD_IA" },
      ]
    }
    # 🥇 Gold：dbt marts，BI 直接讀 → 全程熱存，不轉冷
    marts = {
      noncurrent_version_expiration_days = 30
    }
  }

  tags = {
    Component = "data-lake"
  }
}
