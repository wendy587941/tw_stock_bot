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
  type = list(string)
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
