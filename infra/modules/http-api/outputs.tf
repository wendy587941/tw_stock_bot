output "api_endpoint" {
  description = "HTTP API 根端點（$default stage）"
  value       = aws_apigatewayv2_api.this.api_endpoint
}

output "invoke_url" {
  description = "完整 webhook 入口 URL（根端點 + route 路徑），填入 LINE Developers console webhook 設定"
  # route_key 形如「POST /webhook」，取空白後的路徑段接到根端點
  value = "${aws_apigatewayv2_api.this.api_endpoint}${element(split(" ", var.route_key), 1)}"
}
