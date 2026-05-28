# 台股 AI 投資預警系統 (Taiwan Stock AI Alert System)

> 企業級 Side Project，從 AWS Serverless 架構打通「資料採集 → AI 分析 → LINE 推播」全自動流程。
> 目標：作為 **Cloud Solutions Architect** 與 **Senior Data Engineer** 求職作品集。

[![Status](https://img.shields.io/badge/status-planning-yellow)]()
[![Architecture](https://img.shields.io/badge/arch-AWS%20Serverless-orange)]()
[![IaC](https://img.shields.io/badge/IaC-Terraform-purple)]()
[![AI](https://img.shields.io/badge/AI-Bedrock%20Claude%204.5-ec4899)]()

---

## 📌 專案現況

**目前階段**：規劃定稿（**v3.0**），尚未開始程式碼實作。

**最新規劃書**：[`docs/planning/台股機器人規劃書_v3.html`](docs/planning/台股機器人規劃書_v3.html)（含 Medallion + dbt + Power BI）

**歷史版本**：[`v2`](docs/planning/台股機器人規劃書_v2.html)（不含分析層）

---

## 🏗️ 核心架構（v3 雙軌）

### Hot Path（即時推播 + 查詢）

```
EventBridge (15:30) → Dispatcher → SQS → Worker × N
                                              ↓
                  Bronze (raw JSON)  Silver (Parquet)  DynamoDB
                                                          ↓
                                       Analyzer → Bedrock (Claude 4.5)
                                                          ↓
                                                    SNS → LINE Push

使用者查詢：LINE → API Gateway (HMAC) → Query Lambda → DynamoDB → LINE Reply
```

### Warm Path（分析 + BI）⭐ v3 新增

```
EventBridge (16:00) → dbt run on Athena
                            ↓
                 Silver (Parquet) → Gold (marts)
                            ↓
                      Glue Catalog
                            ↓
                         Athena
                            ↓ (ODBC)
                  Power BI / Tableau Dashboard
```

---

## 🛠️ 技術棧

| 層次 | 技術 |
|---|---|
| **IaC** | Terraform |
| **CI/CD** | GitHub Actions (OIDC) |
| **運算** | AWS Lambda (Docker, ARM Graviton2) |
| **佇列** | Amazon SQS + DLQ |
| **儲存（Hot）** | DynamoDB (GSI) |
| **儲存（Warm/Cold）** | S3 Parquet（Bronze/Silver/Gold 三層）|
| **分析引擎** ⭐ | **Amazon Athena**（SQL on S3, Serverless）|
| **Metadata** ⭐ | **AWS Glue Catalog** |
| **轉換層** ⭐ | **dbt**（Silver → Gold marts）|
| **視覺化** ⭐ | **Power BI / Tableau Public**（ODBC 連 Athena）|
| **AI** | Amazon Bedrock + Claude 4.5 Haiku |
| **密鑰** | AWS Secrets Manager |
| **API** | API Gateway (HTTPS + HMAC) |
| **觸發** | Amazon EventBridge（雙排程：ETL 15:30 + dbt 16:00）|
| **監控** | CloudWatch + X-Ray + SNS |
| **介面** | LINE Messaging API (Push + Webhook) |

**預估月成本**：約 USD $55（v3 加分析層僅多 $0.45）

---

## 📁 專案結構詳解

### 整體鳥瞰

```
tw_stock_bot/
├── docs/                 文件區（給人看的）
├── infra/                基礎設施區（Terraform）
├── src/                  應用程式碼區（Lambda）
├── dbt/                  ⭐ v3 新增：dbt 轉換層
├── bi/                   ⭐ v3 新增：BI 報表檔（Power BI / Tableau）
├── tests/                測試區
├── scripts/              一次性腳本
├── .github/workflows/    CI/CD 設定
├── .gitignore
└── README.md
```

> 本結構參考 **AWS 官方 Lambda 微服務專案** + **HashiCorp Terraform 最佳實務**，符合企業級工程師習慣。面試被問到「你的專案怎麼組織？」可以直接秀這個結構說：「我採用 modules + environments 分離 + Lambda per directory 的設計，每個元件職責單一。」

---

### 🗂️ `docs/` — 文件區（給人看的）

| 子資料夾 | 預留給什麼 |
|---|---|
| `docs/planning/` | **規劃書**。目前放 v2 HTML，未來如果做 v3、v4 都放這。也可以放 PRD（產品需求文件）。 |
| `docs/architecture/` | **架構圖、ADR、Schema 定義**。例如：用 draw.io 畫的細部架構圖、Mermaid 原始碼、ADR（Architecture Decision Record，記錄「為什麼選 SQS 不選 Kinesis」這類重大決策的 trade-off）。 |
| `docs/runbook/` | **維運手冊**。當系統壞了，oncall 工程師要看的故障排查指南。例如：「DLQ 訊息累積怎麼處理」「Bedrock API 429 怎麼擴容」。**這份文件是新加坡遠端職位面試的加分武器。** |

---

### 🏗️ `infra/` — 基礎設施區（Terraform 程式碼）

| 子資料夾 | 預留給什麼 |
|---|---|
| `infra/modules/` | **可重用的 Terraform 模組**。例如：`modules/lambda/`（封裝 Lambda + IAM + CloudWatch 一套）、`modules/sqs/`（SQS + DLQ 一套）、`modules/dynamodb/`（含 GSI 設計）。寫好一次，三個環境共用。 |
| `infra/environments/` | **環境差異化配置**。未來會長出 `dev/`、`staging/`、`prod/` 三個資料夾，每個放自己的 `main.tf`、`variables.tf`、`terraform.tfvars`。同一份 module 代碼，三套不同的 instance 規格與成本配置。 |

> 💡 **Why 分 modules + environments？** 這是 Terraform 最佳實務，避免「修一個地方影響三個環境」的風險。面試會被問到。

---

### 💻 `src/` — 應用程式碼區（Lambda 函式）

| 子資料夾 | 對應規劃書階段 | 預留給什麼 |
|---|---|---|
| `src/dispatcher/` | 階段二入口 | **派發 Lambda**：被 EventBridge 觸發，從 TWSE 取得當日股票清單，逐一塞訊息到 SQS。會有 `handler.py`、`Dockerfile`、`requirements.txt`。 |
| `src/worker/` | 階段二處理 | **ETL Worker Lambda**：被 SQS 觸發，每則訊息處理一支股票（抓資料 → 算技術指標 → 寫 Bronze + Silver + DDB）。包含資料源 Strategy Pattern 實作。 |
| `src/analyzer/` | 階段三 | **AI 分析 Lambda**：被 DynamoDB Stream 觸發，呼叫 Bedrock + Output Validator，把結果寫回 DDB 並發 SNS。 |
| `src/query/` | 階段五 | **LINE Webhook Lambda**：被 API Gateway 觸發，做 HMAC 驗章、查 DDB、回覆 LINE Flex Message。 |

> 💡 **Why 一個 Lambda 一個資料夾？** 因為每個 Lambda 會打包成獨立的 Docker image，各自的 `requirements.txt` 也可能不同（例如 query Lambda 不需要 pandas）。

---

### 📊 `dbt/` — 資料轉換區 ⭐ v3 新增

對應規劃書 **Stage 6 分析層**：用 SQL 把 Silver 層加工為 Gold marts。

| 子資料夾 | 預留給什麼 |
|---|---|
| `dbt/models/staging/` | 對接 S3 Silver 的入口層（型別宣告、輕度清洗）|
| `dbt/models/intermediate/` | 中介計算表（不對外暴露）|
| `dbt/models/marts/` | **🥇 Gold Layer 最終產出**（fct_daily_signals、fct_backtest_returns、dim_stocks）|
| `dbt/tests/` | 自訂資料品質測試（例如「RSI 必須 0-100」）|
| `dbt/macros/` | 可重用 SQL 函式 |
| `dbt/dbt_project.yml` | dbt 專案配置 |
| `dbt/profiles.yml` | Athena 連線設定（Glue Database、Workgroup）|

> 💡 **Why dbt？** Python 處理單筆資料效率高，但跨股票聚合、JOIN、回測勝率用 SQL 寫遠比 pandas 快又清楚。dbt 額外給你版本控制、文件、測試三大紅利。

---

### 📈 `bi/` — BI 報表檔 ⭐ v3 新增

對應規劃書 **Stage 6 BI 整合**：四個核心 Dashboard。

| 子資料夾 | 預留給什麼 |
|---|---|
| `bi/powerbi/` | `.pbix` 檔（Power BI Desktop 編輯的報表）|
| `bi/tableau/` | `.twb` / `.twbx` 檔（Tableau 報表）|

> 💡 **Why git 要存這些檔？** 雖然 `.pbix` 是二進位檔，無法 diff，但仍要納入版控以便回溯、備份、與 README 連結展示。

---

### 🧪 `tests/` — 測試區

未來會分成：
- `tests/unit/`：純函式單元測試（例如 RSI 計算邏輯、HMAC 驗章函式）
- `tests/integration/`：整合測試（用 `moto` mock AWS 服務、用 `pytest-localstack` 跑真實 SQS/DDB）

CI/CD pipeline 跑 `pytest tests/` 必須全綠才能 deploy。

---

### 🔧 `scripts/` — 一次性腳本區

放**不是常駐服務、但偶爾要跑的工具**。例如：

| 腳本 | 用途 |
|---|---|
| `backfill_history.py` | 補抓歷史股價資料 |
| `migrate_ddb_schema.py` | DynamoDB schema 變更搬遷 |
| `cost_report.py` | 拉 AWS Cost Explorer 出本月帳單 |
| `local_test_bedrock.py` | 本機測 Bedrock prompt 調整 |

> 💡 **Why 跟 `src/` 分開？** `src/` 是會部署到 AWS 的程式碼，`scripts/` 是只在本機跑的工具。職責不同。

---

### 🤖 `.github/workflows/` — CI/CD 設定區

GitHub Actions 的 YAML 配置檔。未來會有：

| 檔案 | 用途 |
|---|---|
| `ci.yml` | 每次 push 跑 lint + test |
| `deploy-dev.yml` | merge to `develop` 自動部署 dev 環境 |
| `deploy-prod.yml` | merge to `main` + 人工 approve 部署 prod |
| `terraform-plan.yml` | PR 時自動跑 `terraform plan` 留 comment |

---

### 📄 根目錄檔案

| 檔案 | 用途 |
|---|---|
| `README.md` | 專案門面，給訪客 / 面試官 / 未來的自己看 |
| `.gitignore` | 告訴 git 哪些檔案永遠不要 commit（特別是 `.tfstate`、`.env`、`*.zip`） |

---

### 🚀 從哪裡開始動工？

建議從**階段一 IaC** 的最小單位開始：先寫 `infra/modules/dynamodb/`（含 GSI 設計），這是最簡單的入門點，跑通後再依序展開其他 module。

---

## 🚀 Quick Start（程式碼開發時補上）

```bash
# 待規劃中，將於階段一 IaC 完成後補上
```

---

## 🗺️ 路線圖

### 第一波（2026 Q3）
- [ ] 階段一：Terraform IaC 全資源
- [ ] 階段二：ETL + 資料源 Strategy Pattern
- [ ] 階段三：Bedrock + Output Validator
- [ ] 階段四：CloudWatch 三層告警
- [ ] 階段五：LINE Bot 雙向互動
- [ ] RAG 新聞情緒分析（firecrawl 整合）
- [ ] OpenMetadata 治理整合
- [ ] dbt + Athena 歷史回測

### 第二波（2026 Q4 - 2027 Q1）
- [ ] Multi-Agent 協作架構
- [ ] Streamlit / Next.js Dashboard
- [ ] 英文文件三件套（README / RUNBOOK / COST_ANALYSIS）

### 第三波（2027+）
- [ ] ML 評分層替代規則引擎
- [ ] 跨資產延伸（美股 / 加密貨幣）
- [ ] 多區域災難復原

---

## 📚 設計原則

1. **低 TCO**：純 Serverless + On-Demand 計費，月成本控制在 $60 以內
2. **解耦設計**：EventBridge → SQS → Lambda → DDB Streams → Lambda
3. **容錯機制**：SQS DLQ + 資料源 Strategy Pattern + LLM Output Validation
4. **最小權限**：每個 Lambda 獨立 IAM Role
5. **密鑰管理**：Secrets Manager（杜絕環境變數明文 Token）
6. **合約導向**：階段間 Pydantic Schema 明確定義

---

## 📄 授權

Private project. All rights reserved.
