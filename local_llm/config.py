"""地端 LLM 台股 QA — 全域設定（Week 13）。

秘密（LINE demo channel token/secret）一律讀 Windows 使用者環境變數，不寫明文入庫。
路徑以本檔位置為基準，方便從任何 cwd 執行。
"""
import os
from pathlib import Path

# ── 路徑 ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent            # tw_stock_bot/local_llm
REPO_DIR = BASE_DIR.parent                             # tw_stock_bot
SNAPSHOT_DIR = REPO_DIR / "data" / "snapshot"          # 本機 parquet 快照（.gitignore）
KNOWLEDGE_DIR = BASE_DIR / "knowledge"                 # RAG 語料（繁中 .md）
CHROMA_DIR = BASE_DIR / ".chroma"                      # 向量庫持久化目錄（.gitignore）

# ── Ollama ───────────────────────────────────────────
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen2.5:3b")   # 主力；對照組可切 "breeze2:3b"
EMBED_MODEL = os.environ.get("LOCAL_EMBED_MODEL", "bge-m3")   # 1024 維
EMBED_DIM = 1024

# ── S3 快照來源（既有雲端 marts，唯讀）──────────────────
MARTS_BUCKET = os.environ.get("MARTS_BUCKET", "wendy-tw-stock-bot-marts-ap-northeast-1")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
# dbt-athena 每次 build 把 marts 物化到 athena-results/tables/<uuid>/（UUID 每次變），
# 故 sync 以 Glue Catalog get_table 取「當前」位置，不寫死路徑。
GLUE_DATABASE = os.environ.get("DBT_ATHENA_SCHEMA", "wendy_tw_stock_bot_dev")
# 配息維度（get_dividend）來源＝生產 hot 表的 DIVIDEND#{code}/META（唯讀 scan 匯出）。
HOT_TABLE = os.environ.get("HOT_TABLE", "wendy-tw-stock-bot-hot-dev")

# ── LINE demo channel（獨立第二 channel，勿用生產 channel A）────
# 於 Windows 設：setx LINE_DEMO_CHANNEL_TOKEN "..." / setx LINE_DEMO_CHANNEL_SECRET "..."
LINE_CHANNEL_TOKEN = os.environ.get("LINE_DEMO_CHANNEL_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_DEMO_CHANNEL_SECRET", "")

# ── grounding / 免責 ─────────────────────────────────
DISCLAIMER = "⚠️ 本資訊為程式自動彙整之公開資料，僅供參考，非投資建議。"
