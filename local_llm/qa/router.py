"""Tool-use dynamic-query loop (Week 13, Stage 3).

Give Qwen2.5 the 6 snapshot tools and let *the model* decide which to call for a
question about live-ish market data, then feed the tool output back for a final
natural-language answer. Single-to-few round tool-calling loop:

    question → chat(tools=…) → model returns tool_calls
      → run each tool over the local snapshot → append results
      → chat() again → grounded natural-language answer

Grounding rule (hardened further in Stage 4): every number must come from a tool
result — the model must not invent prices, yields or dividends. Stages 4/5 wrap
this with the static-RAG route and the LINE server.

Run:  python -m local_llm.qa.router "台積電最近走勢"
(needs the repo .venv + Ollama up with qwen2.5:3b; Windows: PYTHONUTF8=1.)
"""

from __future__ import annotations

import json

from local_llm.qa.llm import chat
from local_llm.tools.schemas import TOOLS, execute_tool

SYSTEM = (
    "你是台股盤後資料助理。回答涉及個股股價、走勢、大盤家數、漲跌排行、訊號、"
    "殖利率、配息等『數據型』問題時，必須呼叫提供的工具取得真實數字，"
    "嚴禁自行編造或臆測任何數字。工具回傳 'error' 或空結果時，誠實告知查無資料並建議可用的問法，"
    "不得杜撰。所有數據皆為盤後資料，非即時報價。以繁體中文簡潔作答。"
)

_MAX_ROUNDS = 3


def answer(question: str, *, model: str | None = None) -> dict:
    """跑一輪 tool-use 問答。

    回傳 {"answer": str, "tool_calls": [{name,args,result}], "used_tools": bool}。
    """
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []

    for _ in range(_MAX_ROUNDS):
        msg = chat(messages, tools=TOOLS, model=model)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return {
                "answer": (msg.get("content") or "").strip(),
                "tool_calls": trace,
                "used_tools": bool(trace),
            }
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
    return {
        "answer": (final.get("content") or "").strip(),
        "tool_calls": trace,
        "used_tools": bool(trace),
    }


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "台積電最近走勢"
    out = answer(q)
    print(f"Q: {q}\n")
    for t in out["tool_calls"]:
        print(f"  🔧 {t['name']}({t['args']})")
    print(f"\nA: {out['answer']}")
