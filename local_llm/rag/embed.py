"""Ollama ``bge-m3`` embedding wrapper (Week 13, Stage 2).

A thin HTTP client over Ollama's ``/api/embed`` endpoint — single runtime, fully
offline, no sentence-transformers. Both ``ingest`` (documents) and ``retrieve``
(queries) embed through here so the vector space is identical on both sides.

bge-m3 needs no special query/passage instruction prefix, so queries and
documents are embedded the same way.
"""

from __future__ import annotations

import requests

from local_llm import config


def embed(texts: list[str], *, timeout: int = 120) -> list[list[float]]:
    """回傳每段文字的 bge-m3 向量（1024 維），順序與輸入一致。"""
    if not texts:
        return []
    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/embed",
        json={"model": config.EMBED_MODEL, "input": texts},
        timeout=timeout,
    )
    resp.raise_for_status()
    embeddings = resp.json().get("embeddings")
    if not embeddings or len(embeddings) != len(texts):
        raise RuntimeError(
            f"Ollama embed returned {len(embeddings or [])} vectors for "
            f"{len(texts)} inputs (model={config.EMBED_MODEL})."
        )
    return embeddings


def embed_one(text: str, *, timeout: int = 120) -> list[float]:
    """單段文字的便捷版（查詢端用）。"""
    return embed([text], timeout=timeout)[0]
