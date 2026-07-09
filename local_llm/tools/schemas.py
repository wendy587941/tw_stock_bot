"""Tool (function-calling) schemas for the 6 read-only snapshot queries
(Week 13, Stage 3).

These JSON schemas are handed to Ollama (Qwen2.5 has native function-calling)
so the model itself decides which tool to call for a dynamic question. Each tool
maps 1:1 to a function in ``local_llm.tools.snapshot`` — all read-only over the
local Parquet snapshot, never recomputing anything.

``execute_tool`` runs a model-issued call safely: it keeps only arguments the
target function actually accepts, so a stray/hallucinated argument can't crash
the dispatch.
"""

from __future__ import annotations

import inspect

from local_llm.tools import snapshot

# ── 6 個工具的 JSON schema（Ollama / OpenAI function 格式）─────────────────────
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_ohlcv",
            "description": (
                "查詢單一個股近 N 個交易日的股價走勢，含開高低收、成交量、漲跌幅、"
                "5 日均線(MA5)與 20 日均線(MA20)。問某檔股票『走勢/股價/最近表現/均線』時用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代碼，例如台積電為 '2330'、鴻海 '2317'。",
                    },
                    "days": {
                        "type": "integer",
                        "description": "要回傳的交易日數，預設 20（最新在前）。",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_breadth",
            "description": (
                "查詢某一交易日大盤的漲跌平家數與市場廣度百分比。問『今天/今日大盤、"
                "漲跌家數、市場廣度』時用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "交易日 YYYY-MM-DD；預設 'latest'（快照中最新交易日）。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_movers",
            "description": "查詢某交易日漲幅或跌幅排行前段的個股。問『漲幅榜/跌幅榜/漲最多/跌最多』時用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "交易日 YYYY-MM-DD；預設 'latest'。",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["gainer", "loser"],
                        "description": "gainer=漲幅榜、loser=跌幅榜；預設 gainer。",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "取前幾名，預設 10。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_signals",
            "description": (
                "查詢某交易日的技術訊號排行。kind：gainer=強勢股、loser=弱勢股、"
                "active=爆量/熱門股。問『訊號/強勢股/爆量股』時用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "交易日 YYYY-MM-DD；預設 'latest'。",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["gainer", "loser", "active"],
                        "description": "訊號類型；不指定則回全部類型。",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "每類取前幾名，預設 10。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_yield_ranking",
            "description": "查詢最新交易日的現金殖利率排行前 N 名（含本益比、股價淨值比）。問『殖利率排行/殖利率前幾名』時用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {
                        "type": "integer",
                        "description": "取前幾名，預設 10。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dividend",
            "description": (
                "查詢單一個股的配息資訊：現金股利、除息日、發放日、配息頻率(年配/半年配/季配/月配)。"
                "問『某股配息/除息日/股利』時用。查無則代表尚未公告。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代碼，例如 '2330'。",
                    },
                },
                "required": ["code"],
            },
        },
    },
]

# ── 名稱 → 實際唯讀函式 ───────────────────────────────────────────────────────
DISPATCH = {
    "get_stock_ohlcv": snapshot.get_stock_ohlcv,
    "get_market_breadth": snapshot.get_market_breadth,
    "get_top_movers": snapshot.get_top_movers,
    "get_signals": snapshot.get_signals,
    "get_yield_ranking": snapshot.get_yield_ranking,
    "get_dividend": snapshot.get_dividend,
}

# 全部工具皆屬「盤後數據」查詢（回覆需附資料日期）；靜態知識走確定性 RAG 注入，不當工具。
DATA_TOOLS = frozenset(DISPATCH)


def execute_tool(name: str, args: dict):
    """執行模型指定的工具呼叫；未知工具或多餘參數皆安全處理。"""
    func = DISPATCH.get(name)
    if func is None:
        return {"error": f"unknown tool '{name}'"}
    # 只保留該函式簽章接受的參數，過濾模型可能亂帶的鍵。
    accepted = set(inspect.signature(func).parameters)
    clean = {k: v for k, v in (args or {}).items() if k in accepted}
    try:
        return func(**clean)
    except Exception as e:  # 查詢層錯誤誠實回報，不讓迴圈崩潰
        return {"error": f"{type(e).__name__}: {e}"}
