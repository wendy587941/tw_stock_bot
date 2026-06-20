variable "name" {
  description = "HTTP API 名稱"
  type        = string
}

variable "route_key" {
  description = "路由（method + path）。預設 POST /webhook，對應 LINE webhook 入口。"
  type        = string
  default     = "POST /webhook"
}

variable "lambda_function_name" {
  description = "後端 Lambda 函式名稱（給 aws_lambda_permission 授權 API Gateway 呼叫）"
  type        = string
}

variable "lambda_invoke_arn" {
  description = "後端 Lambda 的 invoke ARN（AWS_PROXY 整合 integration_uri）"
  type        = string
}

variable "tags" {
  description = "附加標籤（與 provider default_tags 合併）"
  type        = map(string)
  default     = {}
}
