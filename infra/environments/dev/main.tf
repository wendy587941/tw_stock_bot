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
    analyzer   = {} # 摘要：讀 hot store 算訊號 + Bedrock 生成每日投資摘要
    notifier   = {} # 推播：讀每日摘要 → LINE Push API 推給使用者
    webhook    = {} # 互動：LINE webhook（Function URL）→ 驗章 → 查摘要/訊號 → Reply
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
    # 國定假日（非交易日）清單，逗號分隔；dispatcher 用來擋掉假日抓到的舊資料寫成髒資料
    MARKET_HOLIDAYS = join(",", var.market_holidays)
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

data "aws_caller_identity" "current" {}

# SSM SecureString 以 AWS 受管金鑰 alias/aws/ssm 加密；解密需對「該金鑰」授 kms:Decrypt
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

# Analyzer Lambda：每日 ETL 後讀 hot store 算訊號，交 Bedrock Claude 生成投資摘要
# 跟著 lambda_image_tag 一起活化/休眠（容器 Lambda 須先有 image）
module "analyzer" {
  source = "../../modules/lambda"
  count  = var.lambda_image_tag != "" ? 1 : 0

  function_name = "${var.project}-analyzer-${var.environment}"
  description   = "讀 hot store 算訊號 + Bedrock 生成每日投資摘要"
  image_uri     = "${module.ecr.repository_urls["analyzer"]}:${var.lambda_image_tag}"
  timeout       = 120 # 含 Bedrock converse 往返，給較寬裕時間

  environment_variables = {
    HOT_TABLE        = module.hot_store.table_name
    MARTS_BUCKET     = module.data_lake.bucket_ids["marts"]
    BEDROCK_MODEL_ID = var.bedrock_model_id
    TOP_N            = tostring(var.top_n)
    # 與 dispatcher 共用同一份非交易日清單，假日 analyzer 空跑不呼叫 Bedrock
    MARKET_HOLIDAYS = join(",", var.market_holidays)
  }

  additional_iam_statements = [
    {
      sid       = "ReadHotStoreByDate"
      actions   = ["dynamodb:Query"]
      resources = [module.hot_store.table_arn, "${module.hot_store.table_arn}/index/GSI1"]
    },
    {
      sid       = "WriteSignalsAndSummary"
      actions   = ["dynamodb:PutItem", "dynamodb:BatchWriteItem"]
      resources = [module.hot_store.table_arn]
    },
    {
      sid       = "WriteMarts"
      actions   = ["s3:PutObject"]
      resources = ["${module.data_lake.bucket_arns["marts"]}/*"]
    },
    {
      # converse 底層走 InvokeModel；跨區推論設定檔需同時授 profile 與其路由的 foundation model
      sid     = "InvokeBedrock"
      actions = ["bedrock:InvokeModel"]
      resources = [
        "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.bedrock_model_id}",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-*",
      ]
    },
  ]

  tags = { Component = "analyzer" }
}

# Notifier Lambda：每日 analyzer 跑完後讀 SUMMARY → 格式化 → LINE Push API 推給使用者
# 跟著 lambda_image_tag 一起活化/休眠（容器 Lambda 須先有 image）
module "notifier" {
  source = "../../modules/lambda"
  count  = var.lambda_image_tag != "" ? 1 : 0

  function_name = "${var.project}-notifier-${var.environment}"
  description   = "讀每日盤勢摘要並透過 LINE Push API 推播"
  image_uri     = "${module.ecr.repository_urls["notifier"]}:${var.lambda_image_tag}"
  timeout       = 30  # 單筆 GetItem + 一次 SSM 讀 + 一次 LINE HTTP 呼叫，很快
  memory_size   = 256 # 無 pandas，最省記憶體

  environment_variables = {
    HOT_TABLE  = module.hot_store.table_name
    SSM_PREFIX = var.line_ssm_prefix
    # 與 dispatcher/analyzer 共用同一份非交易日清單，假日 notifier 空跑不呼叫 LINE
    MARKET_HOLIDAYS = join(",", var.market_holidays)
  }

  additional_iam_statements = [
    {
      sid       = "ReadDailySummary"
      actions   = ["dynamodb:GetItem"]
      resources = [module.hot_store.table_arn]
    },
    {
      # 讀 LINE channel access token（SecureString）與推播目標；限定本專案 line/ 路徑前綴
      sid       = "ReadLineConfig"
      actions   = ["ssm:GetParameter", "ssm:GetParameters"]
      resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.line_ssm_prefix}/*"]
    },
    {
      # 解密 SecureString（AWS 受管 aws/ssm 金鑰），限定該金鑰最小權限
      sid       = "DecryptLineConfig"
      actions   = ["kms:Decrypt"]
      resources = [data.aws_kms_alias.ssm.target_key_arn]
    },
  ]

  tags = { Component = "notifier" }
}

# Webhook Lambda：LINE 互動入口（Function URL）。使用者傳訊 → 驗 X-Line-Signature →
# 查最近交易日 SUMMARY / GSI2 訊號 → Reply。跟著 lambda_image_tag 一起活化/休眠。
module "webhook" {
  source = "../../modules/lambda"
  count  = var.lambda_image_tag != "" ? 1 : 0

  function_name = "${var.project}-webhook-${var.environment}"
  description   = "LINE webhook：驗章 → 查每日摘要/訊號 → Reply"
  image_uri     = "${module.ecr.repository_urls["webhook"]}:${var.lambda_image_tag}"
  timeout       = 15  # 數筆 GetItem + 一次 GSI2 Query + 一次 LINE Reply HTTP，很快
  memory_size   = 256 # 無 pandas，最省

  # 公開 HTTP 入口（免 API Gateway，最低 TCO）；安全由 app 層 X-Line-Signature HMAC 驗章把關
  create_function_url = true

  environment_variables = {
    HOT_TABLE  = module.hot_store.table_name
    SSM_PREFIX = var.line_ssm_prefix
    TOP_N      = tostring(var.top_n)
  }

  additional_iam_statements = [
    {
      sid       = "ReadSummary"
      actions   = ["dynamodb:GetItem"]
      resources = [module.hot_store.table_arn]
    },
    {
      sid       = "QuerySignals"
      actions   = ["dynamodb:Query"]
      resources = [module.hot_store.table_arn, "${module.hot_store.table_arn}/index/GSI2"]
    },
    {
      # 讀 channel_secret（驗章）+ channel_access_token（Reply），限定本專案 line/ 路徑前綴
      sid       = "ReadLineConfig"
      actions   = ["ssm:GetParameter", "ssm:GetParameters"]
      resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.line_ssm_prefix}/*"]
    },
    {
      sid       = "DecryptLineConfig"
      actions   = ["kms:Decrypt"]
      resources = [data.aws_kms_alias.ssm.target_key_arn]
    },
  ]

  tags = { Component = "webhook" }
}

# 每日 ETL 排程：週一至五 15:30（台北、收盤後）觸發 dispatcher 開始當日抓取
# 跟著 dispatcher 一起活化：dispatcher 建立後（lambda_image_tag 有值）排程才建並指向它
# 只排平日 → 週末 Lambda 根本不喚醒，省 invocation（國定假日由 dispatcher 端 MARKET_HOLIDAYS 再擋）
module "schedule_etl" {
  source = "../../modules/eventbridge-schedule"
  count  = length(module.dispatcher) > 0 ? 1 : 0

  schedule_name   = "${var.project}-etl-${var.environment}"
  description     = "週一至五 15:30（台北）觸發 ETL dispatcher"
  cron_expression = "cron(30 15 ? * MON-FRI *)" # 分 時 日 月 週 年；平日 15:30（指定週幾時日需為 ?）
  timezone        = "Asia/Taipei"
  target_arn      = module.dispatcher[0].function_arn
  target_input    = jsonencode({ job = "daily-etl" })

  tags = {
    Component = "schedule"
  }
}

# 每日摘要排程：週一至五 16:00（台北，晚於 ETL 15:30 約 30 分鐘確保資料已落地）觸發 analyzer
# 跟著 analyzer 一起活化：analyzer 建立後（lambda_image_tag 有值）排程才建並指向它
module "schedule_analyze" {
  source = "../../modules/eventbridge-schedule"
  count  = length(module.analyzer) > 0 ? 1 : 0

  schedule_name   = "${var.project}-analyze-${var.environment}"
  description     = "週一至五 16:00（台北）觸發每日投資摘要"
  cron_expression = "cron(0 16 ? * MON-FRI *)"
  timezone        = "Asia/Taipei"
  target_arn      = module.analyzer[0].function_arn
  target_input    = jsonencode({ job = "daily-analyze" })

  tags = {
    Component = "schedule"
  }
}

# 每日推播排程：週一至五 16:30（台北，晚於 analyzer 16:00 約 30 分鐘確保摘要已落地）觸發 notifier
# 跟著 notifier 一起活化：notifier 建立後（lambda_image_tag 有值）排程才建並指向它
module "schedule_notify" {
  source = "../../modules/eventbridge-schedule"
  count  = length(module.notifier) > 0 ? 1 : 0

  schedule_name   = "${var.project}-notify-${var.environment}"
  description     = "週一至五 16:30（台北）觸發每日盤勢 LINE 推播"
  cron_expression = "cron(30 16 ? * MON-FRI *)"
  timezone        = "Asia/Taipei"
  target_arn      = module.notifier[0].function_arn
  target_input    = jsonencode({ job = "daily-notify" })

  tags = {
    Component = "schedule"
  }
}

# Week 6 監控告警：四支 Lambda Errors + DLQ 堆積 + analyzer Bedrock 降級 + notifier 無摘要
# → 彙整單一 SNS topic → Email。Lambda 相關 alarm 跟著 lambda_image_tag 活化（未活化時傳空值不建）。
# SNS topic 與 DLQ alarm 不依賴 Lambda，故 module 恆建立（DLQ 本來就一直存在）。
module "monitoring" {
  source      = "../../modules/monitoring"
  project     = var.project
  alarm_email = var.alarm_email

  # Lambda 活化時才有函式名可監控；四支共用同一 count 旗標，dispatcher 存在即四支皆存在
  lambda_function_names = length(module.dispatcher) > 0 ? {
    dispatcher = module.dispatcher[0].function_name
    worker     = module.worker[0].function_name
    analyzer   = module.analyzer[0].function_name
    notifier   = module.notifier[0].function_name
  } : {}

  dlq_name = module.ingest_queue.dlq_name

  # log group 名稱由 lambda module 輸出（建立正確依賴：metric filter 須等 log group 建好）
  analyzer_log_group_name = length(module.analyzer) > 0 ? module.analyzer[0].log_group_name : ""
  notifier_log_group_name = length(module.notifier) > 0 ? module.notifier[0].log_group_name : ""

  tags = {
    Component = "monitoring"
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
