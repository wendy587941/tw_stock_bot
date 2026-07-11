"""Benchmark question set for the Stage 6 model comparison (Week 13).

A fixed, auto-scorable set of zh-TW questions that exercises every route the
QA pipeline supports:

  * ``data`` — must trigger one of the 6 read-only snapshot tools. Scored on
    whether the model calls ``expect_tool`` (and, when given, with the right
    ``expect_code``). This is the tool-use-reliability signal — the whole point
    of the on-prem design is that numbers come from a query, not from the model.
  * ``rag`` — a concept/rule/FAQ answered from the knowledge base. The router
    retrieves deterministically, so this scores *generation* grounding: did the
    expected source doc get injected as a reference (``expect_source``)?
  * ``oob`` — out of bounds (no local data, or a recommendation we must refuse).
    A good 3B answer downgrades honestly: it must NOT fabricate a data-tool call
    and must NOT falsely cite the knowledge base.

Each item also carries ``note`` — what a human grader should look for when
scoring zh-TW answer quality (the one axis a script can't judge).

Coverage: all 6 tools, all 3 knowledge docs, 3 flavours of out-of-bounds.
"""

from __future__ import annotations

# route ∈ {"data", "rag", "oob"}
QUESTIONS: list[dict] = [
    # ── data route: one question per tool, plus a couple of variants ──────────
    {
        "id": "d1_ohlcv_2330",
        "route": "data",
        "q": "台積電最近的股價走勢如何？",
        "expect_tool": "get_stock_ohlcv",
        "expect_code": "2330",
        "note": "應點名 get_stock_ohlcv(2330)；數字須來自工具，不得杜撰，並附 📅 資料日期。",
    },
    {
        "id": "d2_ohlcv_2317",
        "route": "data",
        "q": "鴻海這幾天的股價表現怎麼樣？",
        "expect_tool": "get_stock_ohlcv",
        "expect_code": "2317",
        "note": "測不同股號抽取；應為 get_stock_ohlcv(2317)。",
    },
    {
        "id": "d3_breadth",
        "route": "data",
        "q": "今天大盤的漲跌家數是多少？",
        "expect_tool": "get_market_breadth",
        "note": "應呼叫 get_market_breadth（date 預設 latest）。",
    },
    {
        "id": "d4_top_gainer",
        "route": "data",
        "q": "今天漲幅榜前五名是哪些股票？",
        "expect_tool": "get_top_movers",
        "note": "應呼叫 get_top_movers(kind=gainer)；名次/漲幅須來自工具。",
    },
    {
        "id": "d5_top_loser",
        "route": "data",
        "q": "今天跌最多的股票有哪些？",
        "expect_tool": "get_top_movers",
        "note": "同一工具但 kind=loser，測參數判讀。",
    },
    {
        "id": "d6_signals",
        "route": "data",
        "q": "今天有哪些強勢股訊號？",
        "expect_tool": "get_signals",
        "note": "應呼叫 get_signals（強勢股 kind=gainer）。",
    },
    {
        "id": "d7_yield",
        "route": "data",
        "q": "目前現金殖利率排行前五的股票是哪些？",
        "expect_tool": "get_yield_ranking",
        "note": "應呼叫 get_yield_ranking(top_n=5)；殖利率數字來自工具。",
    },
    {
        "id": "d8_dividend_2330",
        "route": "data",
        "q": "台積電的配息資訊是什麼？",
        "expect_tool": "get_dividend",
        "expect_code": "2330",
        "note": "應呼叫 get_dividend(2330)；查無則誠實說尚未公告。",
    },
    # ── rag route: one per knowledge doc, plus a concept variant ──────────────
    {
        "id": "r1_yield_concept",
        "route": "rag",
        "q": "什麼是殖利率？",
        "expect_source": "00_glossary.md",
        "note": "純概念題，無數字；應標 📚 並帶出處檔名。",
    },
    {
        "id": "r2_price_limit",
        "route": "rag",
        "q": "台股的漲跌幅限制是多少？",
        "expect_source": "01_tw_market_rules.md",
        "note": "市場規則題；正解為 10%。應引知識庫，不得憑記憶亂答。",
    },
    {
        "id": "r3_bot_faq",
        "route": "rag",
        "q": "這個機器人支援哪些查詢指令？",
        "expect_source": "02_bot_faq.md",
        "note": "FAQ 題；應據 bot_faq 回答支援的指令。",
    },
    {
        "id": "r4_ex_dividend",
        "route": "rag",
        "q": "除權息是什麼意思？",
        "expect_source": "00_glossary.md",
        "note": "術語題；測概念檢索命中。",
    },
    # ── oob route: honest downgrade, no fabricated tool / false citation ──────
    {
        "id": "o1_btc",
        "route": "oob",
        "q": "比特幣明天會漲嗎？",
        "note": "資料範圍外＋預測題；不得呼叫資料工具，須標 ℹ️ 未引用本地資料。",
    },
    {
        "id": "o2_us_stock",
        "route": "oob",
        "q": "美股蘋果現在股價多少？",
        "note": "快照只有台股；應誠實說查無美股資料，不得杜撰數字。",
    },
    {
        "id": "o3_recommend",
        "route": "oob",
        "q": "幫我推薦一支一定會賺錢的股票。",
        "note": "投資建議題；應婉拒／給免責，不保證獲利。",
    },
]


def by_route(route: str) -> list[dict]:
    """回傳指定路徑的題目子集。"""
    return [q for q in QUESTIONS if q["route"] == route]
