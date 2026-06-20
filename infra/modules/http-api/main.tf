# HTTP API（API Gateway v2）→ Lambda（AWS_PROXY）。LINE webhook 公開入口。
# 採 HTTP API 而非 REST API：更便宜（$1/百萬請求、無固定費）、更低延遲、設定更簡。
# 用途上替代 Lambda Function URL（本帳號公開 Function URL 被帳號層級限制 → 改走 API GW 繞過）。
# payload format 2.0 與 Function URL 同格式 → 後端 Lambda handler 不需改。
# 安全：端點公開，由後端 Lambda 的 X-Line-Signature HMAC 驗章把關（與 Function URL 方案一致）。

resource "aws_apigatewayv2_api" "this" {
  name          = var.name
  protocol_type = "HTTP"
  tags          = var.tags
}

# AWS_PROXY 整合：把整個請求原樣轉給 Lambda；payload 2.0 帶 headers/body/isBase64Encoded
resource "aws_apigatewayv2_integration" "this" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.lambda_invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "this" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = var.route_key
  target    = "integrations/${aws_apigatewayv2_integration.this.id}"
}

# $default stage + auto_deploy：免手動 deploy，路由/整合一變更即生效
resource "aws_apigatewayv2_stage" "this" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags
}

# 僅授權「本 API」呼叫該 Lambda（source_arn 鎖到本 API 的 execution ARN，最小權限）
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowInvokeFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
