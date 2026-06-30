variable "project" {
  description = "專案名稱前綴"
  type        = string
}

variable "environment" {
  description = "部署環境（dev/prod）"
  type        = string
}

variable "curated_bucket" {
  description = "Silver 層 bucket 名稱（OHLCV Parquet + 訊號/殖利率 NDJSON 來源）"
  type        = string
}

variable "results_bucket" {
  description = "Athena 查詢結果輸出 bucket 名稱（沿用 marts 層）"
  type        = string
}

variable "projection_date_range_start" {
  description = "partition projection 日期分區起點（資料湖最早日期；NOW 為終點自動滾動）"
  type        = string
  default     = "2025-01-01"
}

variable "bytes_scanned_cutoff_per_query" {
  description = "單次查詢掃描量上限（bytes），成本護欄。預設 1GB。"
  type        = number
  default     = 1073741824
}

variable "tags" {
  description = "資源標籤"
  type        = map(string)
  default     = {}
}
