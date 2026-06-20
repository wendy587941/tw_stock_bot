variable "project" {
  description = "專案名稱前綴"
  type        = string
  default     = "wendy-tw-stock-bot"
}

variable "environment" {
  description = "部署環境（dev/prod）"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-1"
}

variable "lambda_image_tag" {
  description = <<-EOT
    dispatcher / worker 容器 image 的 tag（如 "latest" 或 git sha）。
    這是 Lambda 與排程的總開關：
      - 留空字串 → dispatcher/worker/schedule 全 count=0 不建立
        （容器 Lambda 必須先有 image 才能建，故未 build/push 前保持休眠）
      - 填入已 push 到 ECR 的 tag → 整條 排程→dispatcher→SQS→worker 一次活化
    image_uri 由模組自動組成：<ecr_repo_url>:<lambda_image_tag>
  EOT
  type        = string
  default     = ""
}

variable "market_holidays" {
  description = <<-EOT
    台股國定假日等非交易日清單（YYYY-MM-DD）。注入 dispatcher 的 MARKET_HOLIDAYS env，
    用來擋掉假日抓到的「上一交易日舊資料」被蓋上今天日期寫成髒資料。
    週末已由排程（僅平日觸發）+ dispatcher 週末檢查擋掉，此清單只需填「平日的休市日」。
    請每年依 TWSE 官方行事曆更新：https://www.twse.com.tw/zh/trading/holiday.html
    （留空亦可運作，僅假日當天會多寫一筆重複資料；填了最乾淨。）
  EOT
  type        = list(string)
  # 2026 年（民國 115）平日休市日，來源：TWSE OpenAPI holidaySchedule（官方），
  # 僅收錄落在週一至週五、市場不交易之日（開始/最後交易日等交易日不列入；週末由排程擋掉）。
  default = [
    "2026-01-01", # 中華民國開國紀念日（四）
    "2026-02-12", # 春節前 市場無交易僅結算（四）
    "2026-02-13", # 春節前 市場無交易僅結算（五）
    "2026-02-16", # 農曆春節（一）
    "2026-02-17", # 農曆春節（二）
    "2026-02-18", # 農曆春節（三）
    "2026-02-19", # 農曆春節（四）
    "2026-02-20", # 農曆春節 補假（五）
    "2026-02-27", # 和平紀念日 補假（五）
    "2026-04-03", # 兒童節 補假（五）
    "2026-04-06", # 民族掃墓節 補假（一）
    "2026-05-01", # 勞動節（五）
    "2026-06-19", # 端午節（五）
    "2026-09-25", # 中秋節（五）
    "2026-09-28", # 孔子誕辰／教師節（一）
    "2026-10-09", # 國慶日 補假（五）
    "2026-10-26", # 臺灣光復節 補假（一）
    "2026-12-25", # 行憲紀念日（五）
  ]
}

variable "bedrock_model_id" {
  description = <<-EOT
    analyzer 呼叫的 Bedrock 模型／推論設定檔 ID。ap-northeast-1（東京）新版 Claude 須走
    跨區推論設定檔（日本群組前綴 jp.）。確切 ID 由 `aws bedrock list-inference-profiles
    --region ap-northeast-1` 解析；變動時改此處即可，免改碼。
    前置：須在 Bedrock console 對該模型開啟 model access，否則呼叫回 AccessDenied。
  EOT
  type        = string
  default     = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "top_n" {
  description = "每類訊號（漲幅/跌幅/成交量）取前幾名，注入 analyzer 的 TOP_N env"
  type        = number
  default     = 5
}

variable "alarm_email" {
  description = <<-EOT
    監控告警（CloudWatch alarm → SNS）通知收件 Email。
    Email 非機密，可直接放預設；變更收件人改此值即可。
    ⚠️ 套用後 AWS 會寄一封 SNS 確認信，須點信中連結確認訂閱才會真正收到告警（一次性手動）。
  EOT
  type        = string
  default     = "wendy587941@gmail.com"
}

variable "line_ssm_prefix" {
  description = <<-EOT
    notifier 讀取 LINE 設定的 SSM Parameter Store 路徑前綴（不含結尾斜線）。
    由使用者一次性手動建立（token 用 SecureString，不入版控）：
      <prefix>/channel_access_token （SecureString，**必填**）— LINE channel access token (long-lived)
      <prefix>/push_target          （String/SecureString，**選填**）— 指定推播對象 userId/groupId
    push_target 未設 → notifier 走 broadcast（推給所有 OA followers，自用 bot 免取 userId）；
    有設 → 自動改用 push 指定對象（向後相容，免改碼）。
    建立指令（本機 bash 須 export MSYS_NO_PATHCONV=1 避免開頭 / 被轉成 Windows 路徑）：
      aws ssm put-parameter --type SecureString \
        --name /wendy-tw-stock-bot/dev/line/channel_access_token --value '<token>'
  EOT
  type        = string
  default     = "/wendy-tw-stock-bot/dev/line"
}
