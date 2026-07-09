"""Unified QA pipeline — data tool-use + deterministic RAG (Week 13, Stage 4).

Three routes, but only the numeric one is left to the model:
  - dynamic market numbers → model calls one of the 6 data tools (function-calling);
    numbers may only come from tool results.
  - static concept/rule/FAQ → a **deterministic** RAG step retrieves the knowledge
    base first; on a confident match (distance ≤ KNOWLEDGE_HIT_MAX) the chunks are
    injected as reference material. The 3B model is an unreliable router, so we
    don't let it decide *whether* to look something up — we look it up for it.
  - anything else → answered from the model's general knowledge, and clearly
    labelled as such (see ``prompts.build_footer``).

``respond`` returns the user-facing text with a source label so the reader can
tell whether an answer came from real data (📅), the knowledge base (📚), or is
unverified model knowledge (ℹ️). Stage 5 wires this to LINE.

Run:  python -m local_llm.qa.router "台積電最近走勢"
(needs the repo .venv + Ollama up with qwen2.5:3b; Windows: PYTHONUTF8=1.)
"""

from __future__ import annotations

import json

from local_llm.qa import prompts
from local_llm.qa.llm import chat
from local_llm.rag.retrieve import retrieve
from local_llm.tools.schemas import DATA_TOOLS, TOOLS, execute_tool

SYSTEM = prompts.SYSTEM

_MAX_ROUNDS = 3
# 檢索距離 ≤ 此值才算「知識庫確實命中」→ 注入參考並標 📚。
# 校準：真命中題 0.18–0.36；數字題 0.38+；界外/閒聊 0.51+（有明顯 gap）。
KNOWLEDGE_HIT_MAX = 0.40
_RETRIEVE_K = 3


def answer(question: str, *, model: str | None = None) -> dict:
    """跑一輪問答：先確定性檢索知識庫，再讓模型用資料工具回答。

    回傳 {"answer", "tool_calls", "refs"}；refs 為命中的知識片段（可能為空）。
    """
    hits = retrieve(question, k=_RETRIEVE_K)
    refs = [h for h in hits if h["distance"] <= KNOWLEDGE_HIT_MAX]

    messages: list[dict] = [{"role": "system", "content": SYSTEM}]
    if refs:  # 命中知識庫 → 注入參考資料，讓模型據此作答（不靠 3B 自己決定要不要查）
        messages.append(prompts.build_reference_message(refs))
    messages.append({"role": "user", "content": question})

    trace: list[dict] = []
    for _ in range(_MAX_ROUNDS):
        msg = chat(messages, tools=TOOLS, model=model)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return {"answer": (msg.get("content") or "").strip(), "tool_calls": trace, "refs": refs}
        # 記錄本輪 assistant（含 tool_calls），再逐一執行工具回填。
        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {}) or {}
            if isinstance(args, str):  # 少數情況 arguments 是 JSON 字串
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            result = execute_tool(name, args)
            trace.append({"name": name, "args": args, "result": result})
            messages.append(
                {
                    "role": "tool",
                    "tool_name": name,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

    # 迴圈到頂仍未收斂：用目前 messages 再要一次純文字答案。
    final = chat(messages, model=model)
    return {"answer": (final.get("content") or "").strip(), "tool_calls": trace, "refs": refs}


def respond(question: str, *, model: str | None = None) -> dict:
    """整合對外答覆：跑問答後，依實際來源附上分級標籤＋免責頁尾。

    回傳 {"text", "answer", "tool_calls", "refs", "used_data"}。
    """
    out = answer(question, model=model)
    # 只要有嘗試呼叫資料工具，就視為「走了資料路徑」（查無時無日期，僅附免責）。
    used_data = any(t["name"] in DATA_TOOLS for t in out["tool_calls"])
    footer = prompts.build_footer(out["tool_calls"], out["refs"], used_data=used_data)
    body = out["answer"] or "抱歉，我暫時無法回答這個問題，請換個問法再試一次。"
    out["used_data"] = used_data
    out["text"] = f"{body}\n\n{footer}"
    return out


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "台積電最近走勢"
    out = respond(q)
    print(f"Q: {q}\n")
    for t in out["tool_calls"]:
        print(f"  🔧 {t['name']}({t['args']})")
    print(f"\nA: {out['text']}")
