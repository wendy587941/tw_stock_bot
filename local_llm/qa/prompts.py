"""System prompt + grounding / source-labelling helpers (Week 13, Stage 4).

Centralises the anti-hallucination rules for a public-facing finance Q&A.

Routing:
  - dynamic market numbers → the model calls one of the 6 data tools
    (function-calling); numbers may ONLY come from tool results.
  - static concept/rule/FAQ → a **deterministic** RAG step retrieves the
    knowledge base and, when a confident match exists, injects the chunks as
    reference material (the 3B model is an unreliable router, so we don't let it
    *decide* whether to look something up — we look it up for it).

Every reply carries a source label so the user can tell how trustworthy it is:
  📅 real snapshot data (with data date)
  📚 grounded on the local knowledge base (with source file)
  ℹ️ not backed by local data/knowledge — model general knowledge, verify yourself
"""

from __future__ import annotations

from local_llm import config
from local_llm.tools.schemas import DATA_TOOLS

SYSTEM = (
    "你是台股盤後資料助理，服務對象是一般投資人，回答必須誠實、有根據、不誇大，一律用繁體中文簡潔作答。\n"
    "資料範圍：你只有『台股盤後』資料與一份本地知識庫。**沒有**美股、加密貨幣、匯率、"
    "即時盤中報價或台股以外的資料。\n"
    "規則：\n"
    "1) 涉及數字的問題（個股股價/走勢/均線、大盤漲跌家數、漲跌幅排行、技術訊號、殖利率排行、配息）"
    "→ 必須呼叫對應工具取得真實數字，**嚴禁自行編造、估計或臆測任何數字**。\n"
    "2) 若訊息中附有【參考資料】→ 概念/規則/功能類問題請**只根據參考資料作答**，不要加入資料裡沒有的內容。\n"
    "3) 沒有工具也沒有參考資料可依據時（例如問到美股、加密貨幣、或知識庫未涵蓋的主題）"
    "→ 誠實說明你沒有這項資料、或這超出你的資料範圍，**寧可說不知道也不要編造**，尤其不得捏造任何數字或行情。\n"
    "4) 工具回傳 error 或空結果時，誠實說『查無資料』並建議可用的問法。\n"
    "所有數據皆為盤後資料、非即時報價。不要自行加上免責聲明或資料來源標註，系統會自動附加。"
)


def build_reference_message(refs: list[dict]) -> dict:
    """把檢索命中的知識片段組成一則 system 訊息，注入對話讓模型據以作答。"""
    blocks = []
    for i, r in enumerate(refs, 1):
        blocks.append(f"[參考{i}]（來源：{r['source']}）\n{r['text']}")
    body = "\n\n".join(blocks)
    return {
        "role": "system",
        "content": (
            "【參考資料】以下是本地知識庫中與問題最相關的片段。"
            "若問題屬概念/規則/功能類，請只依據這些內容回答；即時數字仍須用工具查詢。\n\n"
            + body
        ),
    }


def latest_data_date(trace: list[dict]) -> str | None:
    """從工具呼叫軌跡中找出用到的最新盤後資料日期（YYYY-MM-DD）。"""
    dates: list[str] = []
    for call in trace:
        if call.get("name") not in DATA_TOOLS:
            continue
        result = call.get("result")
        rows = result if isinstance(result, list) else [result]
        for row in rows:
            if isinstance(row, dict):
                d = row.get("trade_date")
                if isinstance(d, str) and d:
                    dates.append(d)
    return max(dates) if dates else None


def _dedupe_sources(refs: list[dict]) -> list[str]:
    seen: list[str] = []
    for r in refs:
        label = f"{r['source']} · {r['heading']}"
        if label not in seen:
            seen.append(label)
    return seen


def build_footer(trace: list[dict], refs: list[dict], *, used_data: bool) -> str:
    """依答案的實際來源，組出分級標籤 + 免責頁尾。

    優先序：有用到資料工具 → 📅；否則有注入知識庫參考 → 📚；否則 → ℹ️ 無可靠來源。
    """
    lines: list[str] = []
    if used_data:
        d = latest_data_date(trace)
        if d:
            lines.append(f"📅 資料日期：{d}（盤後）")
    elif refs:
        srcs = _dedupe_sources(refs)
        lines.append("📚 知識來源：" + "；".join(srcs))
    else:
        lines.append(
            "ℹ️ 此回答未引用本地資料或知識庫，為模型一般知識，請自行查證。"
        )
    lines.append(config.DISCLAIMER)
    return "\n".join(lines)
