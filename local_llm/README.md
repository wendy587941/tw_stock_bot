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
- ✅ **階段 2（手刻 RAG 靜態知識庫）已完成** — 3 篇繁中語料 → 27 chunks；bge-m3 → Chroma cosine 檢索，無 LangChain。
- ✅ **階段 3（Tool-use 動態查詢）已完成** — 6 個 function schema + Ollama function-calling 迴圈；數字一律來自工具回傳。
- ✅ **階段 4（整合 QA pipeline）已完成** — 來源分級標籤（📅 資料／📚 知識庫＋檔名／ℹ️ 無可靠來源）。
  3B 當 router 不可靠 → 改「確定性檢索注入」（distance ≤ 0.40），只有數值題才交給 function-calling。
- ✅ **階段 5（LINE 整合）已完成並實機驗收通過** — 2026-07-10
  `line_server.py`（FastAPI）：HMAC-SHA256 驗章 → `respond()` → reply「查詢中」+ loading 動畫 → push 答案。
  離線 smoke test 18 項全綠（驗章／路由／非同步交棒／忙碌降級／例外釋鎖／長答案截斷）；
  手機實測三路徑（📚 RAG／📅 資料工具／ℹ️ 界外題）皆正確回覆，生產 channel A 未受影響。
  對外端點＝Cloudflare Quick Tunnel（`cloudflared tunnel --url http://localhost:8000`）。
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

## 階段 5：LINE 整合設定步驟

> 🔴 **必用獨立的第二個 channel（demo channel B）。** 生產 bot 的 channel A webhook 指向 Lambda Function URL；
> 若把 channel A 的 Webhook URL 改指本機，線上 bot 立刻癱瘓。

### 1. 建立 demo channel（LINE Developers Console）
新建一個 Messaging API channel（Demo 專用 OA）→ 取得 **Channel secret** 與 **Channel access token（long-lived）**。
於 Messaging API 頁籤關閉「自動回應訊息」、開啟「Webhook」。

### 2. 設環境變數（不寫明文入庫）
```
setx LINE_DEMO_CHANNEL_TOKEN  "<demo channel access token>"
setx LINE_DEMO_CHANNEL_SECRET "<demo channel secret>"
```
`setx` 只影響**新開的**終端機，設完請重開 shell。確認：`curl http://localhost:8000/healthz` 應回 `credentials_loaded: true`。

### 3. 起服務
```
ollama serve                                   # 若尚未常駐
python scripts/sync_snapshot.py                # demo 前更新快照（唯一連雲時刻）
uvicorn local_llm.line_server:app --port 8000  # Windows 需 PYTHONUTF8=1
```

### 4. 對外端點（Cloudflare Tunnel，named tunnel＝固定網址）
```
cloudflared tunnel login
cloudflared tunnel create stock-qa-demo
cloudflared tunnel route dns stock-qa-demo stock-qa.<你的網域>
cloudflared tunnel run --url http://localhost:8000 stock-qa-demo
```
固定 hostname 的好處：重開不換網址，免每次回 LINE console 改 Webhook URL。
（無自有網域時可用 ngrok 臨時測試，但免費版網址每次變動。）

### 5. 驗收
1. LINE console 填 Webhook URL＝`https://stock-qa.<你的網域>/callback` → 按 **Verify** 應成功（events 為空、驗章通過即回 200）。
2. 手機加 demo OA 好友 → 傳「今日」「台積電走勢」「什麼是殖利率」→ 皆正確回覆。
3. 慢查詢應看到「🔎 查詢中…」+ 輸入中動畫，答案隨後由 push 補送，**不逾時**。
4. 確認生產 channel A 完全未動（線上 bot 仍正常）。

### 設計要點（面試可講）
| 決策 | 理由 |
|------|------|
| reply token 只回「查詢中」，答案走 push | reply token 時效遠短於 3B + tool-use 的來回時間 |
| 單機 single-flight lock，忙碌直接降級 | 4GB VRAM 跑不動並行推論；排隊只會換成逾時 |
| 收到請求先回 200，QA 丟背景 | LINE webhook 逾時會重送 → 造成重複推論 |
| 驗章沿用生產 webhook 寫法 | HMAC-SHA256 → base64 → `compare_digest`（常數時間比對，防 timing attack） |

## 下一步（階段 6：Breeze2 對照 benchmark）

1. `ollama create` 匯入 Breeze2-3B。
2. `eval/benchmark.py`：同一批問題跑 Qwen vs Breeze2，記錄繁中品質（人工評分）、tool-use 成功率、延遲（tok/s）。
3. 產出比較表寫進 README 當面試素材。

> 每次 demo 前先跑 `python scripts/sync_snapshot.py` 更新快照（需本機 AWS 憑證；跑完即可離線）。
