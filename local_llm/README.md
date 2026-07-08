# 地端 LLM 台股 QA（Week 13）

本機執行的地端（on-prem）對話式台股問答，透過 LINE 回答自然語言提問，答案 grounding 在既有台股機器人累積的真實資料。
**不部署上雲、可離線運行、不觸碰生產 bot 資源。** 定位＝面試展示地端 GenAI 能力。

完整設計見 `../docs/planning/week13_local_llm_qa_規劃書.md`。

---

## 進度

- ✅ **階段 0（環境準備）已完成** — 2026-07-06
- ✅ **階段 1（本機資料快照 S3 marts → DuckDB）已完成** — 2026-07-08
  - `scripts/sync_snapshot.py`：以 Glue `get_table` 解析 marts 當前 S3 位置（dbt 每次 build 換 UUID，故不寫死）→ boto3 下載 CTAS 分片 → DuckDB 合併成 `data/snapshot/*.parquet`（5 marts + DynamoDB 匯出 `dividend.parquet`）。
  - `local_llm/tools/snapshot.py`：6 個唯讀 DuckDB 查詢函式（get_stock_ohlcv / get_market_breadth / get_top_movers / get_signals / get_yield_ranking / get_dividend）。
  - **驗收通過**：6 函式回真實資料；2330 OHLCV（close/ma5/ma20）與雲端 Athena 逐列一致。
- ⬜ 階段 2：手刻 RAG 靜態知識庫
- ⬜ 階段 3：Tool-use 動態查詢
- ⬜ 階段 4：整合 QA pipeline（路由 + grounding）
- ⬜ 階段 5：LINE 整合（獨立 channel + tunnel）
- ⬜ 階段 6（選）：Breeze2 對照 + benchmark
- ⬜ 階段 7：文件 + demo 腳本

## 階段 0 驗收結果（本機實測）

| 項目 | 結果 |
|------|------|
| Ollama | v0.31.1（`winget install Ollama.Ollama`），server `http://localhost:11434` OK |
| 主力模型 | `qwen2.5:3b`（1.9GB），**熱狀態 ≈ 52 tok/s**，繁中生成正常 |
| Embedding | `bge-m3`（1.2GB），維度 **1024** |
| **VRAM（RTX 3050 Ti 4GB）** | qwen 2.2GB + bge-m3 0.66GB＝**約 2.9GB，兩者可同時常駐 GPU（100% GPU）** |
| 結論 | 3B + embedding 在 4GB VRAM 共存無虞；52 tok/s 對 LINE 即時回覆充足 |

> 編碼雷：Windows/Git Bash 用 curl 直送中文 JSON 會亂碼 → 一律用 Python `json.dumps`（ASCII escape）或 `PYTHONUTF8=1`。

## 環境變數（demo 用，勿寫明文入庫）

```
setx LINE_DEMO_CHANNEL_TOKEN  "<demo channel access token>"
setx LINE_DEMO_CHANNEL_SECRET "<demo channel secret>"
```
（AWS 憑證沿用本機既有 profile；`MARTS_BUCKET` 預設已指向現有 marts bucket。）

## 目錄

```
local_llm/
├─ config.py            # 全域設定（路徑/模型/端點/秘密讀環境變數）
├─ requirements.txt     # 相依套件
├─ knowledge/           # RAG 語料（階段 2 撰寫）
├─ rag/                 # ingest.py / retrieve.py（階段 2）
├─ tools/               # snapshot.py / schemas.py（階段 1、3）
├─ qa/                  # router.py / prompts.py / llm.py（階段 3、4）
├─ line_server.py       # FastAPI webhook（階段 5）
└─ eval/                # benchmark.py（階段 6）
```

## 下一步（階段 2：手刻 RAG 靜態知識庫）

1. 撰寫 `knowledge/01_tw_market_rules.md`（台股規則）、`02_bot_faq.md`（bot 功能）。
2. `rag/ingest.py`：chunk → bge-m3 embed → 存 Chroma。
3. `rag/retrieve.py`：query embed → Chroma top-k。
4. 驗收：問「什麼是殖利率」檢索到正確段落。

> 每次 demo 前先跑 `python scripts/sync_snapshot.py` 更新快照（需本機 AWS 憑證；跑完即可離線）。
