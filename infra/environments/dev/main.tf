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

# DynamoDB Hot Store（單表設計：PK/SK + GSI1 按日期 + GSI2 訊號 sparse index）
module "hot_store" {
  source     = "../../modules/dynamodb"
  table_name = "${var.project}-hot-${var.environment}"
  hash_key   = "PK"
  range_key  = "SK"

  # 主鍵 + GSI 鍵屬性（非鍵屬性如 close/volume/score 不需在此宣告）
  attributes = [
    { name = "PK", type = "S" },     # 例：STOCK#2330
    { name = "SK", type = "S" },     # 例：DATE#2026-06-09
    { name = "GSI1PK", type = "S" }, # 例：DATE#2026-06-09
    { name = "GSI1SK", type = "S" }, # 例：STOCK#2330
    { name = "GSI2PK", type = "S" }, # 例：SIGNAL#2026-06-09（僅訊號項目才寫，故稀疏）
    { name = "GSI2SK", type = "S" }, # 例：SCORE#0.85#STOCK#2330
  ]

  global_secondary_indexes = [
    {
      # 查某交易日全部個股特徵
      name            = "GSI1"
      hash_key        = "GSI1PK"
      range_key       = "GSI1SK"
      projection_type = "ALL"
    },
    {
      # Sparse index：只有產生買賣訊號的項目才填 GSI2PK，索引只含訊號 → 查訊號超省
      name            = "GSI2"
      hash_key        = "GSI2PK"
      range_key       = "GSI2SK"
      projection_type = "ALL"
    },
  ]

  ttl_attribute          = "ExpiresAt" # 原始特徵到期自動刪，控 hot store 成本
  point_in_time_recovery = true        # 連續備份，展現維運成熟度
  deletion_protection    = false       # dev 便於 destroy

  tags = {
    Component = "hot-store"
  }
}
