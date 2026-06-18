# ADR-001：IaC 工具選用 Terraform 而非 AWS CloudFormation

| 欄位 | 內容 |
|------|------|
| 狀態 | ✅ 已採納（Accepted） |
| 日期 | 2026-06-12 |
| 決策者 | Wendy |
| 相關元件 | 全專案基礎設施（Lambda / SQS / S3 / DynamoDB / IAM / GitHub OIDC） |

## 背景與問題（Context）

台股 AI 投資預警系統需要以「基礎設施即程式碼（IaC）」管理 AWS 資源，
以達成版本控制、可重現部署與 CI/CD 自動化。AWS 生態系下主要有兩個選項：

1. **Terraform**（HashiCorp，雲中立）
2. **AWS CloudFormation**（AWS 原生）

需在「低 TCO + 高自動化」的專案原則下，選出最適合本專案與長期職涯目標的工具。

## 決策（Decision）

**採用 Terraform 作為唯一 IaC 工具**，搭配：
- 遠端狀態：S3 backend
- 狀態鎖定：DynamoDB lock table
- CI/CD：GitHub Actions + OIDC（免長期憑證）

## 理由（Rationale）

### 1. 跨服務統一管理（最關鍵）
本系統不只有純 AWS 資源，還涉及 **LINE Messaging API**、**GitHub（OIDC、Actions）**，
未來可能加入 Cloudflare / 第三方 provider。CloudFormation 只能管 AWS 資源；
Terraform 透過 provider 機制可用**同一份 IaC 統一管理多方資源**，直接降低維運複雜度。

### 2. 與既有 S3 + DynamoDB 遠端狀態機制契合
專案已建立 S3 backend + DynamoDB lock，是 Terraform 的業界標準遠端狀態做法。
CloudFormation 將 state 封裝於 AWS 黑箱、使用者無法掌控，也學不到此主流協作/鎖定機制。

### 3. 履歷與職涯價值
職涯目標為轉職科技業／新創／新加坡遠端職缺。市場現實：
- 新創與科技業幾乎清一色使用 Terraform（且常為多雲）
- CloudFormation 多見於傳統 AWS-only 大型企業
- 「Terraform + GitHub Actions OIDC + 遠端 state」是國際遠端職缺的硬通貨

### 4. 低 TCO 與可攜性
兩者本身皆免費，但 Terraform 的雲中立性是長期 TCO 保險——
未來若將部分元件搬遷至 GCP / Cloudflare 以省成本，IaC 不需整套重寫。

### 5. 開發體驗
- HCL 比 CloudFormation 的 JSON/YAML 更精簡、可讀性高
- `terraform plan` 的執行前預覽與 drift 偵測體驗優於 CloudFormation change set
- Terraform Registry 提供海量公開 module，減少自寫 nested stack 的成本

## 已評估的替代方案（Considered Alternatives）

### AWS CloudFormation
**優點**：
- 深度整合 AWS，新服務 day-1 支援
- 與 Service Catalog / StackSets 原生整合
- 無需自行管理 state，純 AWS 團隊較省心
- AWS 原生產品，support case 由 AWS 直接負責

**未採用原因**：
這些優點集中於「純 AWS、不想碰 state 管理的大型團隊」情境，
對「個人 side project + 練業界主流技能 + 需多雲彈性 + 需管理第三方 API」的本專案價值偏低。

### AWS CDK（補充）
以程式語言（TypeScript/Python）生成 CloudFormation，開發體驗佳，
但底層仍綁定 CloudFormation、無法跨雲，且本專案重點在練 IaC 宣告式技能而非程式生成，故未採用。

## 影響與後果（Consequences）

**正面**：
- 單一工具管理 AWS + 第三方資源，維運面收斂
- 取得業界主流、履歷加分的技能組合
- 保留未來多雲遷移彈性

**需承擔的取捨**：
- 需自行維護 state（S3 + DynamoDB），增加初期設定成本（已完成）
- AWS 全新服務的 Terraform provider 支援可能略晚於 CloudFormation
- 需注意 state file 安全（含敏感值），須確保 backend 加密與權限控管
