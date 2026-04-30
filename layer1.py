"""
layer1.py
Layer 1 — Safety Management System (SMS) RAG chatbot.

Loads markdown ISM procedures from data/ism_docs, splits them into chunks
along section headings + sentence boundaries, embeds and stores them in
the persistent vector store, and answers procedural questions with
inline source attribution.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Embedder, LLM, VectorStore


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
@dataclass
class Chunk:
    chunk_id: str
    text: str
    source: str       # filename
    section: str      # nearest preceding heading


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _split_into_chunks(text: str, source: str, target_chars: int = 600) -> List[Chunk]:
    """Heading-aware splitter that walks line by line.

    Lines starting with `#` set the current section. Body lines accumulate
    into paragraphs (separated by blank lines). Paragraphs accumulate into
    chunks up to target_chars; we never split a paragraph in the middle.
    """
    chunks: List[Chunk] = []
    current_section = "Document"
    buf: List[str] = []          # paragraphs accumulated for the current chunk
    buf_len = 0
    para: List[str] = []         # lines for the paragraph currently being built
    idx = 0

    def emit_paragraph():
        nonlocal para
        if not para:
            return
        text_block = " ".join(para).strip()
        para = []
        return text_block

    def flush_chunk():
        nonlocal buf, buf_len, idx
        if not buf:
            return
        chunks.append(
            Chunk(
                chunk_id=f"{source}::{idx}",
                text="\n\n".join(buf),
                source=source,
                section=current_section,
            )
        )
        idx += 1
        buf = []
        buf_len = 0

    def add_paragraph(p: str):
        nonlocal buf, buf_len
        if not p:
            return
        if buf_len + len(p) > target_chars and buf:
            flush_chunk()
        buf.append(p)
        buf_len += len(p)

    for raw in text.splitlines():
        line = raw.rstrip()

        m = _HEADING_RE.match(line)
        if m:
            p = emit_paragraph()
            if p:
                add_paragraph(p)
            flush_chunk()                  # one chunk per section
            current_section = m.group(1).strip()
            continue

        if not line.strip():
            p = emit_paragraph()
            if p:
                add_paragraph(p)
            continue

        # Preserve list / numbered lines as their own paragraphs so steps don't merge
        stripped = line.lstrip()
        is_list_line = bool(re.match(r"^(?:[-*]|\d+\.)\s", stripped))
        if is_list_line:
            p = emit_paragraph()
            if p:
                add_paragraph(p)
            add_paragraph(stripped)
        else:
            para.append(stripped)

    p = emit_paragraph()
    if p:
        add_paragraph(p)
    flush_chunk()
    return chunks


# --------------------------------------------------------------------------- #
# SMS RAG
# --------------------------------------------------------------------------- #
class SMSRag:
    """Layer 1: procedural / safety / ISM Q&A."""

    def __init__(self, embedder: Embedder, vstore: VectorStore, llm: LLM,
                 docs_dir: str, top_k: int = 4):
        self.embedder = embedder
        self.vstore = vstore
        self.llm = llm
        self.docs_dir = docs_dir
        self.top_k = top_k

    def ingest(self, force: bool = False) -> int:
        """Embed and add all .md files in docs_dir. Skips if already populated
        unless force=True."""
        existing = len(self.vstore)
        if existing and not force:
            print(f"[layer1] vector store already has {existing} chunks; skipping ingest")
            return existing

        if force and existing:
            # Easiest cross-version approach: delete + recreate
            self.vstore.client.delete_collection(self.vstore.collection.name)
            self.vstore.collection = self.vstore.client.get_or_create_collection(
                name=self.vstore.collection.name
            )

        all_chunks: List[Chunk] = []
        for fname in sorted(os.listdir(self.docs_dir)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(self.docs_dir, fname)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            chunks = _split_into_chunks(text, source=fname)
            all_chunks.extend(chunks)
            print(f"[layer1] {fname}: {len(chunks)} chunks")

        if not all_chunks:
            print("[layer1] no documents found to ingest")
            return 0

        embeddings = self.embedder.encode([c.text for c in all_chunks])
        self.vstore.add(
            ids=[c.chunk_id for c in all_chunks],
            documents=[c.text for c in all_chunks],
            embeddings=embeddings,
            metadatas=[{"source": c.source, "section": c.section} for c in all_chunks],
        )
        print(f"[layer1] ingested {len(all_chunks)} chunks total")
        return len(all_chunks)

    def retrieve(self, question: str):
        q_emb = self.embedder.encode([question])[0]
        return self.vstore.query(q_emb, n_results=self.top_k)

    def answer(self, question: str) -> dict:
        hits = self.retrieve(question)
        if not hits["documents"]:
            return {
                "answer": "I don't have any indexed procedures yet. Run ingest first.",
                "sources": [],
                "context": "",
            }

        # Build a numbered context block with source tags
        context_blocks = []
        sources = []
        for i, (doc, meta) in enumerate(zip(hits["documents"], hits["metadatas"]), start=1):
            tag = f"[{i}] {meta.get('source','?')} — {meta.get('section','?')}"
            context_blocks.append(f"{tag}\n{doc}")
            sources.append({"index": i, "source": meta.get("source"), "section": meta.get("section")})
        context = "\n\n".join(context_blocks)

        system = (
            "You are an assistant for a maritime ship-management Safety Management System. "
            "Answer the user's question using ONLY the procedure excerpts provided. "
            "Cite the bracketed source numbers inline like [1] or [2] after each claim. "
            "If the excerpts do not contain the answer, say so plainly. "
            "Do not invent procedures."
        )
        user = f"Procedure excerpts:\n\n{context}\n\nQuestion: {question}\n\nAnswer:"

        answer = self.llm.instruct(system, user, max_new_tokens=300)
        return {"answer": answer, "sources": sources, "context": context}
