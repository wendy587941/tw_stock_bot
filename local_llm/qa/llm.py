"""Ollama chat/generate wrapper (Week 13, Stage 3).

Thin HTTP client over Ollama's ``/api/chat``. The model is pluggable
(``config.LLM_MODEL``, default Qwen2.5-3B; switch to Breeze2 for the Stage 6
comparison) — the whole point of the on-prem design is "swap the model, data
never leaves the machine".

``chat`` returns the raw assistant *message* dict (so callers can read both
``content`` and ``tool_calls``); ``non_stream`` keeps things simple — a LINE
demo answers one request at a time, no streaming needed.
"""

from __future__ import annotations

import requests

from local_llm import config


def chat(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: int = 300,
) -> dict:
    """呼叫 Ollama chat，回傳 assistant message dict（含 content 與 tool_calls）。"""
    payload: dict = {
        "model": model or config.LLM_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if tools:
        payload["tools"] = tools
    resp = requests.post(f"{config.OLLAMA_HOST}/api/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["message"]
