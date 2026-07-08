# Week 13 規劃書 — 地端 LLM 台股 QA（Ollama + 手刻 RAG + Tool-use + LINE）

> 版本：v1（2026-07-06）｜接手對象：下一位實作 AI Agent（可直接依本書生成 Python / 服務程式 / 設定檔）
> 前置狀態：台股機器人全鏈上線（6 Lambda：dispatcher/worker/analyzer/notifier/webhook/dividend_ingest，本機=remote `70a8d07`，CI 全綠）。
> DynamoDB 熱表（SIGNAL#/YIELD#/DIVIDEND#/DIVFREQ#/SUMMARY#）+ S3 三層資料湖（raw/curated/marts）+ Athena/dbt marts
> （fct_daily_ohlcv / mart_market_breadth / mart_top_movers / fct_signals / fct_yield）皆已存在並每日更新。
> 本書為 **地端 GenAI 問答展示層** epic，與線上雲端服務**完全解耦**、可離線運行，不觸碰任何既有 AWS 生產資源。

---

## 0. 定位與動機（先讀，決定所有取捨）

**這個 epic 的第一目的是「面試展示」，不是營運需求。** 台股機器人的雲端側已用 Amazon Bedrock（Claude）做 AI 摘要，
雲端本就更省更可靠。之所以**另做一套地端（on-prem）LLM**，是因為台灣多數企業（金融、電信、公部門）對雲端 LLM 有
資料外流／法遵疑慮，偏好地端部署。能展示「我在受限硬體上把地端 LLM + RAG + Tool-use + 對話介面一條龍做出來」，
是轉職科技業（尤其資料工程 / AI 落地職缺）的高含金量作品。

**因此本 epic 的優先序＝展示價值 > 低 TCO / Serverless 純度**（與本專案平常的雲端原則相反，這是刻意的）。
但仍守兩條底線：① 地端側**零雲端運算成本**（模型跑在筆電，只有電費）；② **絕不動生產 bot 的 webhook / 資源**。

**面試核心敘事（要在文件與 demo 中講清楚）**：
> 「同一個台股 QA 能力，我做了**雲端（Bedrock）**與**地端（Ollama 自架）**兩套，並能說清楚何種客戶該用哪套——
> 資安敏感者走地端資料主權、要高可用高併發者走雲端。地端架構的核心價值是**模型可插拔**：今天用 Qwen 展示、
> 明天換 Breeze 或任何模型，資料一律不出這台機器。重點是架構，不是綁定哪個模型。」

---

## 1. 目標與範圍（MVP）

打造一個**離線可運行的地端對話式 QA**，透過 LINE 回答使用者對台股現況的自然語言提問，答案一律 grounding 在
既有台股機器人累積的真實資料上。

交付能回答三類問題：
1. **靜態知識**（金融名詞 / 台股規則 / 本 bot 功能）→ 走 **RAG**（手刻 pipeline + 向量檢索）。
   例：「什麼是殖利率？」「除權息參考價怎麼算？」「紅漲綠跌是什麼意思？」「這個 bot 能查什麼？」
2. **動態資料**（個股/大盤即時盤後數據）→ 走 **Tool-use（function calling）查本機資料快照**。
   例：「台積電最近走勢？」「今天漲跌家數多少？」「殖利率前五名？」「2330 什麼時候除息？」
3. **一般推理 / 閒聊**（不需外掛資料）→ 直接用 LLM 通用能力，附免責。

**設計主軸**：沿用本專案「**ETL 先算好、查詢端只讀**」哲學。地端側**不重算**任何指標，只把雲端 marts 的成果
**同步一份快照到本機**，Tool 以 SQL（DuckDB）查快照。全程 demo 時**不依賴網路**（除了 LINE 訊息往返本身）。

**明確不做（後續波次 / 非 MVP，避免發散）**：
- ❌ 自行蒸餾 / fine-tune 模型（4GB VRAM 不可行且非必要，見桌面 brief §3.1）。
- ❌ 導入 LangChain / LangGraph（手刻 pipeline，便於 debug、無框架開銷；見 brief §3.5）。
- ❌ 多用戶高併發 / 24×7 生產級 SLA（單機一次一請求，這是 **demo**，不是取代雲端 bot）。
- ❌ 把地端 LINE 接到**生產 bot 的 channel**（必用獨立第二 channel，見 §7，否則會癱瘓線上 bot）。
- ❌ 即時串流資料 / 盤中 tick（沿用盤後 T+0 快照即可）。
- ❌ 把地端服務部署上雲（本 epic 就是要「留在地端」，違背則失去意義）。

---

## 2. 硬體現況與模型選型（已定案）

**實測硬體（2026-07-06 於本機查得）**：

| 項目 | 規格 | 對模型的意義 |
|------|------|-------------|
| GPU | NVIDIA GeForce RTX 3050 Ti Laptop | 綁死上限的瓶頸 |
| **VRAM** | **4096 MiB（4GB）** | 只能舒服跑 **3B 級**量化模型 |
| 系統 RAM | 15.6GB（≈16GB） | 供 CPU offload / 索引期 embedding |
| 驅動 | 592.00（支援 CUDA） | 可 GPU 加速 |

**模型定案**：

| 角色 | 模型 | 取得方式 | 理由 |
|------|------|---------|------|
| **主力生成** | **Qwen2.5-3B-Instruct（Q4_K_M）** | `ollama pull qwen2.5:3b` | 完整塞進 4GB VRAM、速度快（適合 LINE 即時）、**原生 function-calling**（本 epic 選 Tool-use，這項關鍵）、繁中可用 |
| **在地對照組**（選配加分） | **Breeze2-3B（聯發科，Llama-3.2 基底）** | 下載 GGUF → `ollama create` 匯入 | 台灣模型，強化「不依賴境外」敘事；同批問題與 Qwen benchmark 品質/速度＝資深訊號 |
| **Embedding** | **bge-m3**（多語，繁中佳，1024 維） | `ollama pull bge-m3` | 單一 runtime（都走 Ollama）、離線；替代：`bge-large-zh-v1.5`（sentence-transformers） |

> **VRAM 共存策略（✅ 2026-07-06 階段 0 已實測）**：實測 `qwen2.5:3b`(2.2GB)+`bge-m3`(0.66GB)＝約 2.9GB，
> **兩者可同時常駐 4GB VRAM（皆 100% GPU），無需換入換出**，比原先預期樂觀。熱狀態 qwen ≈ **52 tok/s**（對 LINE 即時充足）。
> 若日後語境/context 拉大或換更大模型才需回到「LLM 常駐、embedding 用完即卸」策略（設 `OLLAMA_KEEP_ALIVE`）。

**面試 talking point**：「我在只有 4GB VRAM 的 laptop 上讓地端 LLM + RAG + Tool-use 跑起來」＝受限硬體優化能力，
比在 24GB 顯卡上隨便跑更能證明工程判斷。把限制講成賣點。

---

## 3. 架構定位與資料流

```
                 【雲端（既有，不改，只讀出）】                          【地端筆電（本 epic 新增，離線可跑）】
S3 marts / Athena marts ──(每日/手動 sync 腳本，唯一連雲時刻)──►  data/snapshot/*.parquet
  fct_daily_ohlcv / mart_market_breadth /                              │（本機快照，之後 demo 全離線）
  mart_top_movers / fct_signals / fct_yield                           ▼
  DynamoDB DIVIDEND#（配息，選）                              ┌──────────────────────────────────┐
                                                             │  地端 QA 服務（FastAPI, 本機）      │
使用者 LINE ──►（獨立第二 channel）──► Cloudflare Tunnel ──► │  1) 路由：靜態/動態/一般            │
                                                             │  2) 靜態 → 手刻 RAG（Chroma+bge-m3）│
                                                             │  3) 動態 → Tool-use（DuckDB 查快照）│
                                                             │  4) Ollama（Qwen2.5-3B）生成+grounding│
                                                             │  5) 免責聲明 → 回 LINE（reply/push） │
                                                             └──────────────────────────────────┘
```

**運算節點**：全部在筆電（Ollama 推論 + FastAPI + Chroma + DuckDB）。**雲端零新增運算**（只沿用既有 S3/Athena 讀出一份快照）。
**唯一對外**：① 每日 sync 腳本讀 S3（可離線 demo 前先跑好）；② LINE 訊息往返（透過 tunnel）。

**與生產 bot 的關係**：**完全獨立**。生產 bot 走 API Gateway → Lambda webhook（channel A）。地端 demo 走
**另一個 LINE channel B** → tunnel → 本機 FastAPI。兩者資料同源（都用台股機器人累積的資料）但服務路徑不交叉。

---

## 4. 資料源盤點與本機快照（沿用既有 marts，不新增擷取）

**來源皆為既有雲端資產**，本 epic 只做「讀出 → 落地成本機快照」：

| 快照檔（本機） | 來源（雲端既有） | 內容 | 對應 Tool |
|----------------|------------------|------|-----------|
| `ohlcv.parquet` | Athena `fct_daily_ohlcv` 或 S3 Silver `curated/date=…/*.parquet` | 每股每日 OHLCV + MA5/MA20 + 漲跌幅 | `get_stock_ohlcv` |
| `market_breadth.parquet` | Athena `mart_market_breadth` | 每日漲跌平家數、廣度% | `get_market_breadth` |
| `top_movers.parquet` | Athena `mart_top_movers` | 每日 Top 漲跌幅（gainer/loser） | `get_top_movers` |
| `signals.parquet` | Athena `fct_signals` | 每日訊號（signal_type/score/rank） | `get_signals` |
| `yield.parquet` | Athena `fct_yield` | 殖利率排行 | `get_yield_ranking` |
| `dividend.parquet`（選） | DynamoDB `DIVIDEND#{code}/META` 匯出 | 個股配息日/現金股利/頻率 | `get_dividend` |

**快照載體＝DuckDB over Parquet**（推薦，取代自建 DB）：
- DuckDB 可**直接查本機 parquet**、零伺服器、單一檔案 / in-process、SQL 介面——與使用者 SQL/ETL 背景完美契合，
  且 DuckDB 是資料工程熱門技術，本身就是履歷加分。
- Tool 函式 = 一段 DuckDB SQL。例：`SELECT trade_date, close, ma5, ma20, volume FROM 'ohlcv.parquet' WHERE code=? ORDER BY trade_date DESC LIMIT ?`。

**同步機制（`scripts/sync_snapshot.py`）**：
- 方式 A（推薦，最省）：用 **boto3 直接下載 S3 marts 的 parquet** 到 `data/snapshot/`（marts 已是 dbt 物化的 Parquet）。
- 方式 B：跑 Athena `UNLOAD ... TO 's3://…' FORMAT PARQUET` 後下載（若要即時聚合）。MVP 用 A。
- 頻率：demo 前手動跑一次即可；可選 Windows 工作排程器每日 18:00 跑（analyzer/dividend 都跑完後）。
- **離線保證**：sync 完成後，整個 QA 服務不再需要網路（除 LINE 往返）。

---

## 5. 手刻 RAG（靜態知識庫）設計

**流程（brief §3.5 手刻，約 100–200 行 Python，無 LangChain）**：
```
docs/*.md（繁中知識）→ chunking（依段落/標題，chunk≈300–500 字，overlap≈50）
  → bge-m3 embedding（Ollama /api/embed）→ 存 Chroma（persistent，本機目錄）
  →〔查詢〕user query → bge-m3 embedding → Chroma cosine top-k（k=3~4）
  → 組 prompt（檢索片段 + 問題）→ Ollama Qwen2.5-3B 生成 → 回答附引用來源
```

**知識庫語料（本機 `local_llm/knowledge/*.md`，自行撰寫，量小、幾十篇短文即可）**：
- 投資名詞：殖利率、本益比（PE）、除權息、現金股利、MA5/MA20、成交量、市場廣度。
- 台股規則：漲跌幅限制 ±10%、**紅漲綠跌**（與歐美相反）、除息參考價與跳空、交易時間、集中市場 vs 上櫃。
- 本 bot 功能說明（雙用：同時當 RAG 語料與使用者 FAQ）：能查什麼、指令對照、資料更新時間、資料限制（ETF 配息缺口等）。
- 免責與資料來源聲明。

> **技術決策**：向量庫用 **Chroma**（persistent client，純 Python、單機、零維運；brief §3.4）。
> embedding 走 Ollama `bge-m3` 保持單一 runtime。**不引入 LangChain**（brief §3.5：抽象層厚、難 debug、版本飄）。

---

## 6. Tool-use / 路由設計（動態查詢）

**路由層（brief §4 路由表的落地）**——先判斷問題類型再分流：

| 問題類型 | 判斷方式 | 處理 |
|----------|----------|------|
| 動態資料（含股號/「今天」/「排行」/「除息」等訊號） | 讓 **LLM function-calling 自己決定**是否叫工具（Qwen2.5 原生支援） | Tool-use 查 DuckDB |
| 靜態知識（名詞/規則/功能） | 無工具可對應時 fallback | RAG 檢索 |
| 一般推理 | 皆不觸發 | 直接生成 |

**實作模式（推薦：讓 LLM 當 router，最能展示 tool-use 能力）**：
把工具定義（JSON schema）與 RAG 檢索都當成「可用工具」交給 Qwen，由模型決定呼叫哪個。單輪 tool-calling loop：
```
使用者問題 → Ollama chat(tools=[...]) → 模型回 tool_calls
  → 本機執行對應 DuckDB 查詢 / Chroma 檢索 → 結果回填 → Ollama 二次生成最終自然語言答案
```

**工具清單（對應 §4 快照，全為唯讀 DuckDB 查詢）**：

| Tool | 參數 | 回傳 | 對應既有雲端指令 |
|------|------|------|------------------|
| `get_stock_ohlcv` | `code`, `days=20` | 近 N 日收盤/MA5/MA20/量 | （新，比雲端更細） |
| `get_market_breadth` | `date=latest` | 漲跌平家數、廣度% | 「今日」 |
| `get_top_movers` | `date=latest`, `kind=gainer\|loser` | Top 漲跌幅 | 「訊號」 |
| `get_signals` | `date=latest`, `kind` | 當日訊號排行 | 「訊號」 |
| `get_yield_ranking` | `top_n=10` | 殖利率排行 | 「殖利率」 |
| `get_dividend` | `code` | 配息日/現金股利/頻率 | 「配息 <股號>」 |

> **設計原則沿用**：這些工具**只讀**本機快照、**不重算**（指標早在雲端 analyzer/dbt 算好）。
> 交易日/最近交易日邏輯：快照取「資料中最新 trade_date」為 `latest`，避免地端重寫交易日曆。

---

## 7. LINE 整合（獨立 channel + tunnel + 非同步）

**🔴 關鍵前提：必須用「獨立的第二個 LINE channel」**，不可指向生產 bot 的 webhook（生產走 API Gateway，
若把同一 channel 的 Webhook URL 改指本機，線上 bot 立即癱瘓）。做法：LINE Developers Console 新建一個
Messaging API channel（Demo 專用 OA），取得該 channel 的 access token / secret。

**對外端點＝Cloudflare Tunnel（推薦）或 ngrok**：
- **Cloudflare Tunnel（named tunnel）**：免費、**固定 hostname**（重開不換網址，免每次改 LINE Webhook URL）。推薦。
- ngrok：更快上手但免費版網址每次變動，適合臨時測試。

**Webhook 服務（`local_llm/line_server.py`，FastAPI）**：
- 驗 `X-Line-Signature`（HMAC-SHA256，沿用生產 webhook 的驗章寫法）。
- 解析事件 → 呼叫本機 QA pipeline → 回覆。
- **reply token 時效（數十秒）處理**：地端 3B + tool-use 來回可能超時 →
  - 簡單/快問答（工具查詢即時）：直接用 **reply API** 回覆。
  - 保險做法（推薦）：先用 reply token 回「🔎 查詢中…」+ 觸發 LINE **loading animation**，pipeline 完成後用
    **push API**（`to`=事件的 userId，該用戶已加 demo OA 好友故可 push）補送答案。**併發**：單機一次一請求，
    超載時回「處理中請稍候」（brief §5.3）。

**環境變數 / 秘密**：demo channel 的 token/secret 放 **Windows 使用者環境變數**（不寫明文入庫；沿用專案原則）。

---

## 8. Grounding / 防幻覺 / 免責（金融場景必做）

開放給真人用戶問股市，**幻覺是最大風險**。硬性規則寫進 system prompt 與程式：
- **數字只能來自工具回傳**：股價、訊號、殖利率、配息一律不得由 LLM 杜撰；工具查無 → 明說「查無資料」並建議可用問法，
  **不得編造**（沿用雲端「待公告」誠實降級精神）。
- **RAG 回答標註來源片段**，超出知識庫範圍就說不知道。
- 每則涉及個股/投資判斷的回覆**固定附**：「⚠️ 本資訊為程式自動彙整之公開資料，僅供參考，非投資建議。」
- 資料時點揭露：回覆帶「資料日期：YYYY-MM-DD（盤後）」，避免用戶誤以為即時。

> 這正好對上你長期經營的賣點「LLM Grounding、防幻覺」，面試可直接當案例講。

---

## 9. TCO 與維運分析（依專案慣例保留此節）

| 項目 | 成本 / 複雜度 | 說明 |
|------|--------------|------|
| 地端推論（Ollama/Qwen） | **≈ $0**（僅電費） | 無雲端 token 費、無 GPU 租用 |
| 向量庫 / 快照（Chroma/DuckDB） | $0 | 本機檔案，零伺服器 |
| S3 快照下載 | ≈ $0 | 沿用既有 marts，下載量小（幾 MB/日） |
| Tunnel | $0 | Cloudflare Tunnel / ngrok 免費額度 |
| **維運複雜度** | **中（demo 定位可接受）** | 筆電需在 demo 時開機 + tunnel 起；**非 24×7 服務**故不追高可用 |
| 自動化風險 | 低 | 純本機，無破壞雲端資源之虞（唯讀 S3） |

**對照雲端（Bedrock）**：雲端 = 按 token 計費但高可用、自動 scale、免顧機器。**這張對照表本身就是面試素材**——
展示你懂「地端省 token/顧資料主權，但換來單機維運與可用性成本」的取捨。

---

## 10. 目錄結構與檔案清單（建議，置於 repo `local_llm/`，與 Lambda `src/` 明確分離）

```
tw_stock_bot/
├─ local_llm/                      # 地端 demo，全部本機執行，不部署上雲
│  ├─ README.md                    # 啟動步驟、demo 腳本、面試 talking points
│  ├─ requirements.txt             # fastapi, uvicorn, chromadb, duckdb, ollama(py), boto3, requests
│  ├─ config.py                    # 模型名、路徑、Ollama endpoint、LINE channel 設定（讀環境變數）
│  ├─ knowledge/                   # RAG 語料（繁中 .md，自行撰寫）
│  │  ├─ 00_glossary.md            # 投資名詞
│  │  ├─ 01_tw_market_rules.md     # 台股規則（±10%、紅漲綠跌、除息…）
│  │  └─ 02_bot_faq.md             # 本 bot 功能/指令/資料限制
│  ├─ rag/
│  │  ├─ ingest.py                 # chunk → embed(bge-m3) → 存 Chroma
│  │  └─ retrieve.py               # query embed → Chroma top-k
│  ├─ tools/
│  │  ├─ snapshot.py               # DuckDB 連線 + 6 個 query 函式
│  │  └─ schemas.py                # 6 個 tool 的 JSON schema（給 Ollama function-calling）
│  ├─ qa/
│  │  ├─ router.py                 # 動態/靜態/一般 分流 + tool-calling loop
│  │  ├─ prompts.py                # system prompt（grounding 規則、免責）
│  │  └─ llm.py                    # Ollama chat/embed 封裝（Qwen / Breeze2 可切換）
│  ├─ line_server.py               # FastAPI webhook（驗章 → QA → reply/push）
│  └─ eval/
│     └─ benchmark.py              # 同批問題跑 Qwen vs Breeze2，比品質/延遲（面試加分）
├─ scripts/
│  └─ sync_snapshot.py             # S3 marts → data/snapshot/*.parquet
└─ data/snapshot/                  # 本機快照（.gitignore，不入庫）
```

---

## 11. Work Breakdown（階段化交付，每階段可獨立驗收）

> 沿用本專案「小步、每步可驗證」節奏。地端無 CI/部署鏈，改以「本機驗收命令」把關。

### 階段 0：環境準備
- 交付：安裝 Ollama；`ollama pull qwen2.5:3b`、`ollama pull bge-m3`；建 `local_llm/` 骨架與 `requirements.txt`。
- 驗收：`ollama run qwen2.5:3b "你好"` 有回應；`nvidia-smi` 顯示模型佔用 VRAM（確認吃到 GPU）；記錄實測 tok/s。

### 階段 1：本機資料快照（`scripts/sync_snapshot.py` + `tools/snapshot.py`）
- 交付：boto3 下載 S3 marts parquet → `data/snapshot/`；DuckDB 6 個 query 函式。
- 驗收：`python -c "from local_llm.tools.snapshot import get_top_movers; print(get_top_movers())"` 印出當日真實 Top 漲跌；
  各函式回傳與雲端 Athena 查詢一致（抽 2330 對數字）。

### 階段 2：手刻 RAG 靜態知識庫（`knowledge/` + `rag/`）
- 交付：撰寫 3 篇繁中語料；`ingest.py` 建 Chroma；`retrieve.py` top-k 檢索。
- 驗收：`ingest.py` 建庫成功；問「什麼是殖利率」檢索到 glossary 正確段落；離線（斷網）可跑。

### 階段 3：Tool-use 動態查詢（`tools/schemas.py` + `qa/llm.py` + `qa/router.py` 的 tool loop）
- 交付：6 個 tool JSON schema；Ollama function-calling 迴圈（模型選工具 → 執行 → 二次生成）。
- 驗收：CLI 問「台積電最近走勢」→ 模型正確叫 `get_stock_ohlcv(code=2330)` → 答案數字與快照一致；
  問「殖利率前五」→ 叫 `get_yield_ranking(top_n=5)`。**數字零杜撰**（比對快照）。

### 階段 4：整合 QA pipeline（`qa/router.py` + `prompts.py`）
- 交付：三路分流（動態 tool / 靜態 RAG / 一般）；grounding system prompt；免責與資料日期附加。
- 驗收：混合問答集（動態×5、靜態×5、一般×2）跑一輪，人工檢查：動態數字正確、靜態有引用、查無誠實降級、每則有免責。

### 階段 5：LINE 整合（獨立 channel + `line_server.py` + tunnel）
- 交付：新建 demo LINE channel；FastAPI 驗章 + QA + reply/push（含「查詢中」+ push 非同步）；Cloudflare Tunnel 固定網址；
  token/secret 入 Windows 環境變數。
- 驗收：LINE console Webhook Verify 成功；手機實傳「今日」「台積電走勢」「什麼是殖利率」皆正確回覆；
  慢查詢走「查詢中→push」不逾時。**確認未動生產 channel A。**

### 階段 6（選配，面試加分）：Breeze2 對照 + benchmark（`eval/benchmark.py`）
- 交付：`ollama create` 匯入 Breeze2-3B；同一批問題跑 Qwen vs Breeze2，記錄品質（人工評分）與延遲（tok/s）。
- 驗收：產出一張比較表（繁中品質 / tool-use 成功率 / 延遲），寫進 README 當面試素材。

### 階段 7：文件 + demo 腳本（`local_llm/README.md`）
- 交付：一鍵啟動步驟（ollama serve → sync → uvicorn → tunnel）、3 分鐘 demo 腳本、面試 talking points、
  架構圖、對照雲端 TCO 表；（選）錄一段 demo 影片放履歷。
- 驗收：照 README 從乾淨狀態能重現整套 demo。

---

## 12. 待確認 / 風險

| # | 項目 | 說明 / 建議 |
|---|------|-------------|
| 1 | Breeze2-3B 的 Ollama 取得方式 | 需確認官方/社群 GGUF 連結與 chat template；若 tool-use 支援弱，Breeze2 僅供「繁中生成品質」對照，動態查詢仍以 Qwen 為主 |
| 2 | 4GB VRAM 下 3B + tool-use 延遲 | 階段 0 先量 tok/s；若 LINE 逾時頻繁，一律走「查詢中→push」非同步 |
| 3 | demo LINE channel 額度 | Messaging API 免費方案有月推播上限；自用 demo 足夠，注意別和生產 OA 混用 |
| 4 | 快照鮮度 | demo 前務必先跑 `sync_snapshot.py`；可加「資料日期」揭露避免誤解 |
| 5 | 中文編碼 | Windows 讀寫中文語料/log 需 `PYTHONUTF8=1`（沿用本專案 dbt 經驗） |
| 6 | 電競筆電散熱/續航 | 長時間 demo GPU 滿載會發熱，面試 demo 前插電、預熱模型（先 warm 一次） |

---

## 13. 與既有專案的關係（一句話總結）

- **雲端生產 bot**（Lambda + Bedrock + 生產 LINE channel A）：**完全不動**。
- **地端 demo**（本 epic：Ollama + RAG + DuckDB 快照 + demo LINE channel B）：新增、獨立、離線、零雲端運算成本、唯讀既有 marts。
- 兩者資料同源、能力對照，共同構成「**同一 QA 能力，雲/地兩套，能講清取捨**」的面試敘事。
```
