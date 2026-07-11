# 地端 LLM 台股 QA — 啟動與模型切換 Runbook

本機（on-prem）demo 的操作手冊：怎麼把服務跑起來、怎麼在 **Qwen2.5-3B ↔ Breeze2-3B** 兩個模型間切換、以及踩過的雷。
架構與設計說明見 [`README.md`](./README.md)；模型選型實測見 README〈階段 6〉。

> 環境：Windows 11 + **PowerShell**。所有指令在 PowerShell 執行（不是 Git Bash）。
> 專案根：`C:\Users\wendytsai\Documents\wd_agent\tw_stock_bot`（以下 `<repo>` 代稱）。

---

## 0. 前置確認（每次開機後看一眼）

| 項目 | 確認指令 | 期望 |
|------|---------|------|
| Ollama 常駐 | `curl http://localhost:11434/api/tags` | 列出 `qwen2.5:3b`、`breeze2:3b`、`bge-m3` |
| venv | `Test-Path <repo>\.venv\Scripts\python.exe` | `True` |
| 快照 | `Test-Path <repo>\data\snapshot\ohlcv.parquet` | `True`（缺 → 見 §5） |
| 知識庫 | `Test-Path <repo>\local_llm\.chroma` | `True`（缺 → 見 §5） |
| LINE 憑證 | `[Environment]::GetEnvironmentVariable("LINE_DEMO_CHANNEL_SECRET","User")` | 非空（長度 32） |
| ngrok 網域 | `[Environment]::GetEnvironmentVariable("NGROK_DEMO_DOMAIN","User")` | `conductor-slightly-regalia.ngrok-free.dev` |

> Ollama 為開機常駐服務，通常不必手動啟動。若 `add-authtoken` / `ngrok` 報 command not found，多半是裝完**沒重開 shell**。

---

## 1. 只跑本機 QA（CLI，不對外，最快驗證模型活著）

```powershell
cd C:\Users\wendytsai\Documents\wd_agent\tw_stock_bot
$env:PYTHONUTF8 = "1"        # Windows 不設會中文亂碼
.venv\Scripts\python.exe -m local_llm.qa.router "台積電走勢"
```

會印出模型呼叫的工具與最終答案（末尾帶 📅/📚/ℹ️ 來源標籤）。這條**完全離線**，不對外、不需要 ngrok。

---

## 2. 完整 LINE demo（對外，手機可用）

### 方式 A：一鍵腳本（推薦）

```powershell
cd C:\Users\wendytsai\Documents\wd_agent\tw_stock_bot
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -SkipSync
```

腳本會：預熱模型 → 另開視窗起 uvicorn → 等 `/healthz` 就緒 → 前景起 ngrok（固定網域）。
- `-SkipSync`：跳過連雲更新快照（快照還新時用）。要更新資料就拿掉這個旗標（需本機 AWS 憑證）。
- 收工：ngrok 視窗按 **Ctrl+C**；uvicorn 視窗要**另手動關閉**。

### 方式 B：手動兩視窗（要精細控制、或切模型時用）

**視窗 1 — webhook：**
```powershell
cd C:\Users\wendytsai\Documents\wd_agent\tw_stock_bot
$env:PYTHONUTF8 = "1"
.venv\Scripts\uvicorn.exe local_llm.line_server:app --port 8000
```

**視窗 2 — ngrok（固定網域）：**
```powershell
ngrok http --domain=conductor-slightly-regalia.ngrok-free.dev 8000
```
> ngrok ≥ 3.16 也可用 `--url=`；舊版只認 `--domain=`（本機已升到 3.39.9，兩者皆可）。

### 確認整條鏈路

```powershell
# 本機
curl http://localhost:8000/healthz
# 外部（LINE 會走的路徑）
curl -H "ngrok-skip-browser-warning: 1" https://conductor-slightly-regalia.ngrok-free.dev/healthz
```
兩者都要回 `{"ok":true, "model":"...", "credentials_loaded":true, "busy":false}`。
外部回 `ERR_NGROK_3200 offline` = ngrok agent 沒在跑（視窗 2 沒起）。

之後在 LINE console（**demo channel B**）按 **Verify** 應通過；手機加好友傳「台積電走勢／什麼是殖利率／比特幣會漲嗎」實測。

---

## 3. 切換語言模型（Qwen2.5-3B ↔ Breeze2-3B）

**機制**：`config.py` 以環境變數 **`LOCAL_LLM_MODEL`** 決定要用哪個模型（預設 `qwen2.5:3b`）。
**關鍵**：這個值在 **python/uvicorn 啟動時讀取**——所以必須**在起服務之前**設好，起起來之後再改環境變數**不會生效**。

可選值（Ollama 內已安裝的 tag）：

| 值 | 模型 | 特性（見 README〈階段 6〉） |
|----|------|------------------------------|
| `qwen2.5:3b` | Qwen2.5-3B（預設） | 原生 function-calling 開箱即用、快（~55 tok/s）|
| `breeze2:3b` | Breeze2-3B（MediaTek 在地化）| 繁中純度高，但慢、tool-use 會印成文字不走原生通道 |

### 3-1. CLI 臨時切換（只影響當前視窗）

```powershell
$env:LOCAL_LLM_MODEL = "breeze2:3b"      # 切到 Breeze2
.venv\Scripts\python.exe -m local_llm.qa.router "台積電走勢"

$env:LOCAL_LLM_MODEL = "qwen2.5:3b"      # 切回 Qwen
# 或直接清掉，回到預設：
Remove-Item Env:\LOCAL_LLM_MODEL
```

### 3-2. LINE demo 切換（在起 uvicorn 的視窗先設）

用**方式 B** 的視窗 1，起 uvicorn **之前**加一行：
```powershell
$env:PYTHONUTF8 = "1"
$env:LOCAL_LLM_MODEL = "breeze2:3b"      # ← 起服務前設定
.venv\Scripts\uvicorn.exe local_llm.line_server:app --port 8000
```
確認生效：
```powershell
curl http://localhost:8000/healthz       # "model" 欄應顯示 breeze2:3b
```
切回 Qwen：關掉 uvicorn（Ctrl+C）→ 清掉或改回 `$env:LOCAL_LLM_MODEL="qwen2.5:3b"` → 重起 uvicorn。

> **一鍵腳本 + 切模型**：在執行 `start_demo.ps1` 的**同一視窗**先 `$env:LOCAL_LLM_MODEL="breeze2:3b"` 再跑腳本，新開的 uvicorn 視窗會繼承這個環境變數。要確認就看 `/healthz` 的 `model` 欄。

### 3-3. 永久改預設（不建議，會影響所有情境）

```powershell
setx LOCAL_LLM_MODEL "breeze2:3b"        # 寫進 User 環境變數；設完須重開 shell
# 還原：setx LOCAL_LLM_MODEL "qwen2.5:3b"  或到系統設定刪掉該變數
```

---

## 4. 兩模型對照 benchmark（怎麼比、怎麼重跑）

同一條生產管線只換模型，量 tool-use / RAG / 誠實降級 / 延遲 / tok-s：
```powershell
$env:PYTHONUTF8 = "1"
.venv\Scripts\python.exe -m local_llm.eval.benchmark qwen2.5:3b breeze2:3b
```
結果（逐題 JSON + 人工評分表）寫到 `local_llm\eval\results\`（已 gitignore）。彙總表會印在終端機，並已整理進 README〈階段 6〉。

---

## 5. 資料面重建（快照 / 知識庫）

```powershell
$env:PYTHONUTF8 = "1"
# 快照：從雲端 marts 同步到本機（唯一連雲步驟，需 AWS 憑證）
.venv\Scripts\python.exe scripts\sync_snapshot.py
# 知識庫：把 knowledge\*.md 重新 embed 進 Chroma（冪等，可重跑）
.venv\Scripts\python.exe -m local_llm.rag.ingest
```

---

## 6. 停止 / 收工

- webhook（uvicorn 視窗）：**Ctrl+C**。
- ngrok 視窗：**Ctrl+C**（一關，對外立即 offline）。
- Ollama 是常駐服務，通常不用關。

---

## 7. 疑難排解（實際踩過）

| 症狀 | 原因 | 解法 |
|------|------|------|
| LINE Verify error、外部回 `ERR_NGROK_3200 offline` | ngrok agent 沒在跑（只有雲端 domain，本機沒接上） | 起視窗 2 的 `ngrok http --domain=... 8000` |
| `ngrok: unknown flag: --url` | ngrok 舊版（<3.16）沒有 `--url` | 改用 `--domain=`，或 `ngrok update` |
| `/healthz` 空、埠 8000 沒在聽 | uvicorn 沒起 | 起視窗 1 的 uvicorn |
| Verify 回 403 / log 印 `invalid X-Line-Signature` | env 的 `LINE_DEMO_CHANNEL_SECRET` 與 Verify 的 channel 不同一個 | 確認 secret 屬於 **demo channel B**；設完 `setx` 要重開 shell |
| `healthz.credentials_loaded=false` | uvicorn 所在 shell 沒吃到憑證環境變數 | `setx LINE_DEMO_CHANNEL_TOKEN/SECRET` 後**重開 shell** 再起 uvicorn |
| 切了模型但 `/healthz` 的 `model` 沒變 | `LOCAL_LLM_MODEL` 是啟動時讀取 | 先設環境變數**再**起 uvicorn；改了要重起服務 |
| 中文變亂碼 | 沒設 `PYTHONUTF8` | 起服務前 `$env:PYTHONUTF8="1"`（PowerShell 用 `$env:`，不是 `set`） |
| `ngrok` / `python` 在 Git Bash 找不到 | Git Bash PATH ≠ Windows PATH | 用 PowerShell，或給完整路徑 |
| Breeze2 某題卡到 300s timeout | 4GB VRAM 下 Breeze2 + bge-m3 互搶顯存 | demo 主力用 Qwen；Breeze2 僅對照用 |

---

## 常用網址速查

- 本機健康檢查：`http://localhost:8000/healthz`
- ngrok 本地儀表板（看 tunnel 狀態 / 重放請求）：`http://localhost:4040`
- 對外 Webhook URL（填 LINE console，固定不變）：`https://conductor-slightly-regalia.ngrok-free.dev/callback`
