"""Dependency-free BM25 retriever over SOP documents.

Swappable for a vector store (pgvector / FAISS) behind the same `search` interface.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SOP_DIR = Path(__file__).resolve().parent.parent / "data" / "sops"
_TOKEN = re.compile(r"[a-z0-9_]+")
_STOP = {"the", "a", "an", "and", "or", "to", "of", "for", "is", "in", "on", "with", "if", "then", "it", "be", "by"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Doc:
    doc_id: str
    title: str
    text: str


@dataclass
class Hit:
    doc: Doc
    score: float


class SOPRetriever:
    def __init__(self, docs: list[Doc], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d.title + " " + d.text)) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = sum(self.lens) / max(1, len(self.lens))
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    @classmethod
    def from_dir(cls, path: Path = SOP_DIR) -> SOPRetriever:
        docs = []
        for p in sorted(path.glob("*.md")):
            text = p.read_text()
            title = text.splitlines()[0].lstrip("# ").strip()
            docs.append(Doc(doc_id=title.split(":")[0], title=title, text=text))
        return cls(docs)

    def search(self, query: str, k: int = 3) -> list[Hit]:
        q = tokenize(query)
        hits = []
        for doc, tf, ln in zip(self.docs, self.tfs, self.lens, strict=True):
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
            if s > 0:
                hits.append(Hit(doc, s))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
