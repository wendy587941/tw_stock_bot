"""Query the RAG vector store (Week 13, Stage 2).

Embed the user question with bge-m3, then cosine top-k against the Chroma
collection built by ``ingest.py``. Returns the matching knowledge chunks with
their source, so the generation step (Stage 4) can cite where each fact came
from and stay grounded.

Run:  python -m local_llm.rag.retrieve "什麼是殖利率"
"""

from __future__ import annotations

import chromadb

from local_llm import config
from local_llm.rag.embed import embed_one
from local_llm.rag.ingest import COLLECTION


def retrieve(query: str, k: int = 4) -> list[dict]:
    """回傳與 query 最相近的前 k 個知識片段（distance 越小越相近）。"""
    client = chromadb.PersistentClient(path=config.CHROMA_DIR.as_posix())
    try:
        col = client.get_collection(COLLECTION)
    except Exception as e:
        raise RuntimeError(
            f"knowledge collection '{COLLECTION}' not found — "
            "run `python -m local_llm.rag.ingest` first."
        ) from e

    res = col.query(
        query_embeddings=[embed_one(query)],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    return [
        {
            "text": doc,
            "source": meta.get("source"),
            "heading": meta.get("heading"),
            "distance": dist,
        }
        for doc, meta, dist in zip(docs, metas, dists)
    ]


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "什麼是殖利率"
    print(f"query: {q}\n")
    for i, hit in enumerate(retrieve(q), 1):
        print(f"[{i}] {hit['source']} · {hit['heading']}  (distance={hit['distance']:.4f})")
        print(hit["text"])
        print("-" * 60)
