"""Small retrieval primitives: BM25, RRF, MMR, TTL cache."""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple


def tokenize(text: str) -> List[str]:
    t = re.sub(r"\s+", "", str(text or ""))
    grams = {t[i:i + 2] for i in range(max(0, len(t) - 1))}
    words = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[a-zA-Z]{2,}", str(text or "")))
    return list(grams | words)[:60]


def bm25_scores(docs: Sequence[str], query: str, k1: float = 1.5, b: float = 0.75) -> List[float]:
    if not docs:
        return []
    corpus = [tokenize(d) for d in docs]
    q = tokenize(query)
    n = len(corpus)
    avg = sum(len(d) for d in corpus) / max(1, n)
    df = Counter()
    for d in corpus:
        for term in set(d):
            df[term] += 1
    scores = []
    for d in corpus:
        tf = Counter(d)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf[term] + k1 * (1.0 - b + b * len(d) / max(1.0, avg))
            s += idf * (tf[term] * (k1 + 1.0)) / max(1e-9, denom)
        scores.append(s)
    return scores


def rrf_fuse(rankings: Sequence[Sequence[int]], k: int = 60) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            out[int(idx)] = out.get(int(idx), 0.0) + 1.0 / (k + rank + 1)
    return out


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def mmr_select(texts: Sequence[str], scores: Sequence[float], k: int, lam: float = 0.7) -> List[int]:
    """Return indices selected by MMR; deterministic tie-break by score/index."""
    n = len(texts)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (-scores[i], i))
    selected: List[int] = []
    remaining = set(order)
    while remaining and len(selected) < k:
        best, best_val = None, -1e18
        for i in remaining:
            rel = float(scores[i])
            div = max((_jaccard(texts[i], texts[j]) for j in selected), default=0.0)
            val = lam * rel - (1.0 - lam) * div
            if val > best_val:
                best, best_val = i, val
        selected.append(best)
        remaining.discard(best)
    return selected


class TTLCache:
    def __init__(self, ttl: float = 45.0, max_size: int = 256):
        self.ttl = float(ttl)
        self.max_size = int(max_size)
        self.data: Dict[object, Tuple[float, object]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        item = self.data.get(key)
        if not item:
            self.misses += 1
            return None
        ts, value = item
        if time.time() - ts > self.ttl:
            self.data.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key, value) -> None:
        if len(self.data) >= self.max_size:
            oldest = min(self.data.items(), key=lambda kv: kv[1][0])[0]
            self.data.pop(oldest, None)
        self.data[key] = (time.time(), value)

    def clear(self) -> None:
        self.data.clear()

    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self.data)}
