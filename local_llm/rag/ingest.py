"""Build the RAG vector store from ``knowledge/*.md`` (Week 13, Stage 2).

Hand-rolled pipeline, no LangChain (plan §5): read the Traditional-Chinese
knowledge docs, chunk them by Markdown heading, embed each chunk with bge-m3
(via Ollama), and store into a persistent Chroma collection.

Re-running is idempotent: the collection is dropped and rebuilt each time, so
editing a knowledge file and re-ingesting always reflects the latest text.

Run:  python -m local_llm.rag.ingest
(needs Ollama up with bge-m3; Windows: prefix ``PYTHONUTF8=1``.)
"""

from __future__ import annotations

import re

import chromadb

from local_llm import config
from local_llm.rag.embed import embed

COLLECTION = "tw_stock_knowledge"

# 目標 chunk 大小（中文字數，plan §5：≈300–500 字、overlap≈50）。
_TARGET_CHARS = 450
_OVERLAP_CHARS = 60


def _split_sections(md: str) -> list[tuple[str, str]]:
    """把 Markdown 依 '## ' 標題切成 (heading, body) 段落。

    '# ' 一級標題視為文件標題，不單獨成段。
    """
    lines = md.splitlines()
    doc_title = ""
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("# ") and not line.startswith("## "):
            doc_title = line[2:].strip()
            continue
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    out: list[tuple[str, str]] = []
    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        full_heading = f"{doc_title}｜{heading}" if doc_title else heading
        out.append((full_heading, body))
    return out


def _chunk_body(body: str) -> list[str]:
    """把單一段落的內文切成 ~450 字、重疊 ~60 字的 chunk（依句子邊界）。"""
    if len(body) <= _TARGET_CHARS:
        return [body]
    # 依中文/英文句末標點切句，避免切在句子中間。
    sentences = re.split(r"(?<=[。！？；\n])", body)
    sentences = [s for s in (t.strip() for t in sentences) if s]
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + len(s) > _TARGET_CHARS:
            chunks.append(cur)
            cur = cur[-_OVERLAP_CHARS:] + s  # 帶入尾端重疊，保留上下文
        else:
            cur += s
    if cur:
        chunks.append(cur)
    return chunks


def build_chunks() -> list[dict]:
    """讀 knowledge/*.md → 產出 chunk 清單（含 text 與 metadata）。"""
    chunks: list[dict] = []
    files = sorted(config.KNOWLEDGE_DIR.glob("*.md"))
    if not files:
        raise FileNotFoundError(
            f"no *.md knowledge files under {config.KNOWLEDGE_DIR}"
        )
    for path in files:
        md = path.read_text(encoding="utf-8")
        for heading, body in _split_sections(md):
            for i, piece in enumerate(_chunk_body(body)):
                # 每個 chunk 前綴標題，讓檢索片段自帶主題脈絡。
                text = f"【{heading}】\n{piece}"
                chunks.append(
                    {
                        "id": f"{path.stem}::{heading}::{i}",
                        "text": text,
                        "source": path.name,
                        "heading": heading,
                    }
                )
    return chunks


def ingest() -> int:
    """建立（或重建）Chroma 知識庫，回傳寫入的 chunk 數。"""
    chunks = build_chunks()
    texts = [c["text"] for c in chunks]
    print(f"embedding {len(texts)} chunks with {config.EMBED_MODEL} ...")
    vectors = embed(texts)

    client = chromadb.PersistentClient(path=config.CHROMA_DIR.as_posix())
    # 重建：先刪舊 collection 再建，確保與最新語料一致。
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    col.add(
        ids=[c["id"] for c in chunks],
        embeddings=vectors,
        documents=texts,
        metadatas=[{"source": c["source"], "heading": c["heading"]} for c in chunks],
    )
    return len(chunks)


if __name__ == "__main__":
    n = ingest()
    print(f"✅ ingested {n} chunks into Chroma at {config.CHROMA_DIR}")
