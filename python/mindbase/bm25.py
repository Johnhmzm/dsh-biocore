from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Sequence


def _tokens(text: str) -> List[str]:
    text = (text or "").lower()
    chars = [text[i:i + 2] for i in range(max(0, len(text) - 1))]
    words = [w for w in text.replace("\n", " ").split() if len(w) > 1]
    return chars + words


class BM25:
    def __init__(self, docs: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.docs = list(docs)
        self.k1 = k1
        self.b = b
        self.freqs: List[Counter] = [Counter(_tokens(d)) for d in self.docs]
        self.lengths = [sum(f.values()) for f in self.freqs]
        self.avgdl = sum(self.lengths) / max(1, len(self.lengths))
        df: Dict[str, int] = {}
        for f in self.freqs:
            for t in f:
                df[t] = df.get(t, 0) + 1
        n = max(1, len(self.docs))
        self.idf = {t: math.log(1 + (n - v + 0.5) / (v + 0.5)) for t, v in df.items()}

    def score(self, query: str, doc_index: int) -> float:
        q = Counter(_tokens(query))
        f = self.freqs[doc_index]
        dl = self.lengths[doc_index] or 1
        total = 0.0
        for term, qf in q.items():
            tf = f.get(term, 0)
            if not tf:
                continue
            denom = tf + self.k1 * (1 - self.b + self.b * dl / max(1e-6, self.avgdl))
            total += self.idf.get(term, 0.0) * tf * (self.k1 + 1) / denom * qf
        return total

    def rank(self, query: str, k: int = 5) -> List[int]:
        scores = [(self.score(query, i), i) for i in range(len(self.docs))]
        scores.sort(reverse=True)
        return [i for _, i in scores[:k]]
