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

# 派工佇列：EventBridge 觸發 dispatcher → 把每檔股票代號 fan-out 進此佇列 → worker Lambda 消費
# 標準佇列即可（個股處理彼此獨立、不需順序）；失敗訊息自動進 DLQ 隔離
module "ingest_queue" {
  source     = "../../modules/sqs"
  queue_name = "${var.project}-ingest-${var.environment}"

  # worker 單檔 ETL 預估 < 60s，隱藏時間設 6 倍緩衝避免處理中被重複取出
  visibility_timeout_seconds = 360
  max_receive_count          = 5 # 重試 5 次仍失敗 → 轉 DLQ

  tags = {
    Component = "ingest-queue"
  }
}

# ECR：存放 dispatcher / worker 的容器 image（Lambda Docker 部署來源）
module "ecr" {
  source  = "../../modules/ecr"
  project = var.project

  repositories = {
    dispatcher = {} # 派工：列出個股 → fan-out 進 ingest queue
    worker     = {} # 消費：抓取單檔 → 寫 Bronze/Silver + DynamoDB
  }

  tags = {
    Component = "container-registry"
  }
}

# Dispatcher Lambda：被 EventBridge 觸發 → 列出當日個股 → fan-out 送進 ingest queue
# 守門：lambda_image_tag 留空時 count=0 不建立（容器 Lambda 須先有 image 才能建）
module "dispatcher" {
  source = "../../modules/lambda"
  count  = var.lambda_image_tag != "" ? 1 : 0

  function_name = "${var.project}-dispatcher-${var.environment}"
  description   = "列出當日個股並 fan-out 進 ingest queue"
  image_uri     = "${module.ecr.repository_urls["dispatcher"]}:${var.lambda_image_tag}"
  timeout       = 120 # 派工含外部 API 取個股清單，給較寬裕時間

  environment_variables = {
    INGEST_QUEUE_URL = module.ingest_queue.queue_url
  }

  # 僅授「送訊到 ingest 佇列」最小權限
  additional_iam_statements = [{
    sid       = "FanOutToIngestQueue"
    actions   = ["sqs:SendMessage"]
    resources = [module.ingest_queue.queue_arn]
  }]

  tags = { Component = "dispatcher" }
}

# Worker Lambda：被 ingest queue 觸發 → 抓單檔 → 寫 Bronze(raw JSON)+Silver(Parquet)+DynamoDB
module "worker" {
  source = "../../modules/lambda"
  count  = var.lambda_image_tag != "" ? 1 : 0

  function_name = "${var.project}-worker-${var.environment}"
  description   = "消費 ingest queue，抓取並寫入 Bronze/Silver 與 hot store"
  image_uri     = "${module.ecr.repository_urls["worker"]}:${var.lambda_image_tag}"
  timeout       = 60 # < ingest queue visibility 360s

  environment_variables = {
    RAW_BUCKET     = module.data_lake.bucket_ids["raw"]
    CURATED_BUCKET = module.data_lake.bucket_ids["curated"]
    HOT_TABLE      = module.hot_store.table_name
  }

  # 由 ingest queue 觸發（消費權限由 module 依此旗標自動附掛）
  create_sqs_event_source = true
  sqs_queue_arn           = module.ingest_queue.queue_arn
  sqs_batch_size          = 10

  # 業務最小權限：寫 Bronze/Silver 物件 + 寫 hot store
  additional_iam_statements = [
    {
      sid       = "WriteDataLake"
      actions   = ["s3:PutObject"]
      resources = ["${module.data_lake.bucket_arns["raw"]}/*", "${module.data_lake.bucket_arns["curated"]}/*"]
    },
    {
      sid       = "WriteHotStore"
      actions   = ["dynamodb:PutItem", "dynamodb:BatchWriteItem"]
      resources = [module.hot_store.table_arn]
    },
  ]

  tags = { Component = "worker" }
}

# 每日 ETL 排程：15:30（台北、收盤後）觸發 dispatcher 開始當日抓取
# 跟著 dispatcher 一起活化：dispatcher 建立後（lambda_image_tag 有值）排程才建並指向它
module "schedule_etl" {
  source = "../../modules/eventbridge-schedule"
  count  = length(module.dispatcher) > 0 ? 1 : 0

  schedule_name   = "${var.project}-etl-${var.environment}"
  description     = "每日 15:30（台北）觸發 ETL dispatcher"
  cron_expression = "cron(30 15 * * ? *)" # 分 時 日 月 週 年；每天 15:30
  timezone        = "Asia/Taipei"
  target_arn      = module.dispatcher[0].function_arn
  target_input    = jsonencode({ job = "daily-etl" })

  tags = {
    Component = "schedule"
  }
}

# GitHub Actions OIDC：讓 CI 以短期憑證假冒 role，無需把 AWS 長期金鑰存進 GitHub Secrets
module "github_oidc" {
  source    = "../../modules/iam-github-oidc"
  role_name = "${var.project}-gha-${var.environment}"

  # 信任整個 repo（PR 跑 plan、main 跑 apply 皆可）
  subjects = ["repo:wendy587941/tw_stock_bot:*"]

  # managed_policy_arns 預設 AdministratorAccess（學習階段，與本機 admin user 一致，上線前收斂）
  tags = {
    Component = "ci-cd"
  }
}
