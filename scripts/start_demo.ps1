<#
.SYNOPSIS
    地端 LLM 台股 QA — 一鍵啟動 demo（Week 13 階段 5/6）。

.DESCRIPTION
    把 demo 前置一次帶起來：更新快照 → 預熱模型 → 起 FastAPI webhook
    （另開視窗）→ 用 ngrok 的「固定網域」對外（前景執行，Ctrl+C 收工）。

    因為用的是 ngrok reserved static domain，對外網址每次都一樣，
    LINE console 的 Webhook URL 填一次就好，重開機／重跑都不用再改。

    只碰 demo channel B，完全不動生產 channel A（其 webhook 掛在 Lambda）。

.PARAMETER Domain
    你的 ngrok 固定網域（免費帳號自動配，格式 xxxxx.ngrok-free.dev）。
    省略時讀環境變數 NGROK_DEMO_DOMAIN（建議：setx NGROK_DEMO_DOMAIN "..."）。

.PARAMETER Port
    本機 uvicorn 埠，預設 8000。

.PARAMETER SkipSync
    跳過 scripts/sync_snapshot.py（唯一連雲步驟）。純離線重跑、或快照仍新時用。

.PARAMETER NoWarm
    跳過模型預熱。預熱可避免 demo 第一題吃到 GPU 冷載入而變慢。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -Domain xxxxx.ngrok-free.dev

.EXAMPLE
    # 已設好 NGROK_DEMO_DOMAIN，離線重跑（不更新快照）
    .\scripts\start_demo.ps1 -SkipSync
#>
[CmdletBinding()]
param(
    [string]$Domain = $env:NGROK_DEMO_DOMAIN,
    [int]$Port = 8000,
    [switch]$SkipSync,
    [switch]$NoWarm
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"   # Windows 上不設會中文亂碼

# 以腳本位置回推 repo 根，讓從任何 cwd 執行都對。
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Uvicorn = Join-Path $RepoRoot ".venv\Scripts\uvicorn.exe"

function Fail($msg) { Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }

# ── 前置檢查 ──────────────────────────────────────────────
if (-not $Domain) {
    Fail @"
沒有固定網域。請擇一：
  1) 帶參數：  .\scripts\start_demo.ps1 -Domain xxxxx.ngrok-free.dev
  2) 設環境變數（一次即可，之後免帶）： setx NGROK_DEMO_DOMAIN "xxxxx.ngrok-free.dev"（設完重開 shell）
在 ngrok dashboard → Domains 建立免費固定網域後取得。
"@
}
if (-not (Test-Path $Py))      { Fail "找不到 venv：$Py（先建好 tw_stock_bot\.venv 並裝 requirements）" }
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Fail "找不到 ngrok。安裝：winget install ngrok.ngrok（裝完重開 shell），並 ngrok config add-authtoken <token>。"
}

# Ollama 常駐檢查（Windows 版開機自啟；沒起的話 QA 會失敗）。
try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5 | Out-Null }
catch { Write-Host "⚠️  Ollama 沒回應（localhost:11434）。若非常駐請先另開視窗 `ollama serve`。" -ForegroundColor Yellow }

# ── 1. 更新快照（唯一連雲時刻，需本機 AWS 憑證）──────────────
if ($SkipSync) {
    Write-Host "⏭️  跳過快照更新（-SkipSync）。" -ForegroundColor DarkGray
} else {
    Write-Host "🔄 更新本機快照（sync_snapshot.py，唯一連雲步驟）…" -ForegroundColor Cyan
    try { & $Py "scripts\sync_snapshot.py" }
    catch { Write-Host "⚠️  快照更新失敗，改用既有快照續跑：$($_.Exception.Message)" -ForegroundColor Yellow }
}

# ── 2. 預熱模型（避免第一題冷啟）─────────────────────────────
if ($NoWarm) {
    Write-Host "⏭️  跳過模型預熱（-NoWarm）。" -ForegroundColor DarkGray
} else {
    Write-Host "🔥 預熱模型（跑一題丟棄）…" -ForegroundColor Cyan
    try { & $Py "-m" "local_llm.qa.router" "台積電走勢" | Out-Null }
    catch { Write-Host "⚠️  預熱失敗（不致命，續跑）：$($_.Exception.Message)" -ForegroundColor Yellow }
}

# ── 3. 起 webhook（另開視窗，保持開啟）───────────────────────
Write-Host "🚀 啟動 webhook（新視窗）：uvicorn local_llm.line_server:app --port $Port" -ForegroundColor Cyan
$uvArgs = "-NoExit", "-Command",
          "`$env:PYTHONUTF8='1'; Set-Location '$RepoRoot'; & '$Uvicorn' local_llm.line_server:app --port $Port"
Start-Process powershell -ArgumentList $uvArgs -WindowStyle Normal

# 等 healthz 就緒再開 tunnel（最多 ~30s）。
Write-Host "⏳ 等待 http://localhost:$Port/healthz …" -ForegroundColor Cyan
$ready = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod "http://localhost:$Port/healthz" -TimeoutSec 2
        if ($h.ok) {
            $ready = $true
            if (-not $h.credentials_loaded) {
                Write-Host "⚠️  healthz.credentials_loaded=false —— LINE demo channel 憑證沒載入。" -ForegroundColor Yellow
                Write-Host "    請確認已 setx LINE_DEMO_CHANNEL_TOKEN / LINE_DEMO_CHANNEL_SECRET 並重開 shell。" -ForegroundColor Yellow
            }
            break
        }
    } catch { }
}
if (-not $ready) { Fail "webhook 未就緒（healthz 逾時）。看新開的 uvicorn 視窗有無錯誤。" }
Write-Host "✅ webhook 就緒。" -ForegroundColor Green

# ── 4. 對外（ngrok 固定網域，前景執行；Ctrl+C 收工）──────────
$WebhookUrl = "https://$Domain/callback"
Write-Host ""
Write-Host "🌐 對外固定網址（填進 LINE console Webhook URL，一次即可）：" -ForegroundColor Green
Write-Host "    $WebhookUrl" -ForegroundColor Green
Write-Host "   （只設 demo channel B，勿動生產 channel A）" -ForegroundColor DarkGray
Write-Host ""
Write-Host "▶️  啟動 ngrok（Ctrl+C 結束對外；uvicorn 視窗需另手動關閉）…" -ForegroundColor Cyan
& ngrok http "--url=$Domain" $Port
