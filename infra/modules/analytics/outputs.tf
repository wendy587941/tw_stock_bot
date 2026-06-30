output "database_name" {
  description = "Glue Catalog database 名稱（dbt schema / Tableau 連線用）"
  value       = aws_glue_catalog_database.analytics.name
}

output "workgroup_name" {
  description = "Athena workgroup 名稱（dbt / Tableau 連線用）"
  value       = aws_athena_workgroup.primary.name
}

output "table_names" {
  description = "已建立的外部表清單"
  value = [
    aws_glue_catalog_table.silver_ohlcv.name,
    aws_glue_catalog_table.signals.name,
    aws_glue_catalog_table.yield_ranking.name,
  ]
}
