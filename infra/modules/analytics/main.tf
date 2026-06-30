###############################################################################
# Analytics 層：Athena 無伺服器 SQL over S3 Silver（Warm/分析路徑，與線上熱路徑解耦）
#
#   - Glue Catalog Database：dbt-athena 與 Tableau 共用的 schema
#   - Athena Workgroup     ：查詢結果輸出 + 成本護欄（bytes cutoff）
#   - 3 張外部表（partition projection，免 Glue Crawler、新分區即時可查、零維運）：
#       silver_ohlcv  ← curated/date=YYYY-MM-DD/*.parquet（worker 寫，Parquet）
#       signals       ← curated/signals/date=YYYY-MM-DD/signals.json（analyzer 寫，NDJSON）
#       yield_ranking ← curated/yield/date=YYYY-MM-DD/yield.json（dividend_ingest 寫，NDJSON）
###############################################################################

locals {
  db_name        = replace("${var.project}_${var.environment}", "-", "_") # Glue/Athena 識別字不接受 '-'
  curated_s3     = "s3://${var.curated_bucket}"
  json_serde     = "org.openx.data.jsonserde.JsonSerDe"
  parquet_serde  = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
  parquet_input  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
  parquet_output = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
  text_input     = "org.apache.hadoop.mapred.TextInputFormat"
  text_output    = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

  # 各表共用的 partition projection 參數（date 分區自動推導，免 Crawler/MSCK）
  projection_base = {
    "projection.enabled"            = "true"
    "projection.date.type"          = "date"
    "projection.date.format"        = "yyyy-MM-dd"
    "projection.date.range"         = "${var.projection_date_range_start},NOW"
    "projection.date.interval"      = "1"
    "projection.date.interval.unit" = "DAYS"
    "EXTERNAL"                      = "TRUE"
  }
}

resource "aws_glue_catalog_database" "analytics" {
  name        = local.db_name
  description = "台股機器人分析層 schema（Athena/dbt/Tableau 共用）"
}

resource "aws_athena_workgroup" "primary" {
  name = "${var.project}-${var.environment}"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query

    result_configuration {
      output_location = "s3://${var.results_bucket}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  force_destroy = true # dev 便於 destroy
  tags          = var.tags
}

# ── silver_ohlcv：每股每日 OHLCV（Parquet）──────────────────────────────────
resource "aws_glue_catalog_table" "silver_ohlcv" {
  database_name = aws_glue_catalog_database.analytics.name
  name          = "silver_ohlcv"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.projection_base, {
    "classification"            = "parquet"
    "storage.location.template" = "${local.curated_s3}/curated/date=$${date}/"
  })

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.curated_s3}/curated/"
    input_format  = local.parquet_input
    output_format = local.parquet_output

    ser_de_info {
      serialization_library = local.parquet_serde
    }

    columns {
      name = "code"
      type = "string"
    }
    columns {
      name = "name"
      type = "string"
    }
    columns {
      name = "trade_date"
      type = "string"
    }
    columns {
      name = "open"
      type = "double"
    }
    columns {
      name = "high"
      type = "double"
    }
    columns {
      name = "low"
      type = "double"
    }
    columns {
      name = "close"
      type = "double"
    }
    columns {
      name = "volume"
      type = "double"
    }
  }
}

# ── signals：每日訊號逐列（NDJSON）─────────────────────────────────────────
resource "aws_glue_catalog_table" "signals" {
  database_name = aws_glue_catalog_database.analytics.name
  name          = "signals"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.projection_base, {
    "classification"            = "json"
    "storage.location.template" = "${local.curated_s3}/curated/signals/date=$${date}/"
  })

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.curated_s3}/curated/signals/"
    input_format  = local.text_input
    output_format = local.text_output

    ser_de_info {
      serialization_library = local.json_serde
    }

    columns {
      name = "trade_date"
      type = "string"
    }
    columns {
      name = "signal_type"
      type = "string"
    }
    columns {
      name = "rank"
      type = "int"
    }
    columns {
      name = "code"
      type = "string"
    }
    columns {
      name = "name"
      type = "string"
    }
    columns {
      name = "close"
      type = "double"
    }
    columns {
      name = "volume"
      type = "double"
    }
    columns {
      name = "pct_change"
      type = "double"
    }
    columns {
      name = "score"
      type = "double"
    }
  }
}

# ── yield_ranking：每日殖利率排行逐列（NDJSON）─────────────────────────────
resource "aws_glue_catalog_table" "yield_ranking" {
  database_name = aws_glue_catalog_database.analytics.name
  name          = "yield_ranking"
  table_type    = "EXTERNAL_TABLE"

  parameters = merge(local.projection_base, {
    "classification"            = "json"
    "storage.location.template" = "${local.curated_s3}/curated/yield/date=$${date}/"
  })

  partition_keys {
    name = "date"
    type = "string"
  }

  storage_descriptor {
    location      = "${local.curated_s3}/curated/yield/"
    input_format  = local.text_input
    output_format = local.text_output

    ser_de_info {
      serialization_library = local.json_serde
    }

    columns {
      name = "trade_date"
      type = "string"
    }
    columns {
      name = "rank"
      type = "int"
    }
    columns {
      name = "code"
      type = "string"
    }
    columns {
      name = "name"
      type = "string"
    }
    columns {
      name = "dividend_yield"
      type = "double"
    }
    columns {
      name = "pe_ratio"
      type = "double"
    }
    columns {
      name = "pb_ratio"
      type = "double"
    }
  }
}
