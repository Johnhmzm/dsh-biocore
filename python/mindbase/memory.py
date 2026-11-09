from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from .config import MindConfig
from .noise import clip, jaccard, sigmoid


@dataclass
class MemoryItem:
    text: str
    importance: float
    t: float
    hits: int = 0
    chars: Set[str] = None  # type: ignore
    bigrams: Set[str] = None  # type: ignore
    last_access: float = 0.0
    confidence: float = 0.50
    semanticized: bool = False
    source: str = "episodic"
    valence: float = 0.0
    arousal: float = 0.5

    def __post_init__(self):
        if self.chars is None:
            self.chars = set(self.text)
        if self.bigrams is None:
            self.bigrams = {self.text[i:i + 2] for i in range(max(0, len(self.text) - 1))}
        if self.last_access <= 0.0:
            self.last_access = self.t


class SparseMemory:
    """Sparse associative memory with DA-gated writes and top-1 retrieval.

    Mirrors the mushroom-body motif: a cue activates few items, one wins.
    """

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.items: List[MemoryItem] = []
        self.current_valence = 0.0
        self.current_arousal = 0.5
        self.affect_weight = float(getattr(cfg, "memory_affect_weight", 0.0))

    def set_affect(self, valence: float, arousal: float) -> None:
        self.current_valence = clip(float(valence), -1.0, 1.0)
        self.current_arousal = clip(float(arousal), 0.0, 1.0)

    def activation_components(
        self, item: MemoryItem, cue_chars: Set[str], cue_bigrams: Set[str], now: float
    ) -> Dict[str, float]:
        age = max(0.0, now - item.last_access)
        recency = 0.5 ** (age / max(self.cfg.memory_half_life, 1e-6))
        importance = float(item.importance)
        frequency = min(1.0, math.log1p(item.hits) / math.log1p(10))
        relevance = 0.7 * jaccard(item.bigrams, cue_bigrams) + 0.3 * jaccard(item.chars, cue_chars)
        return {
            "recency": clip(recency, 0.0, 1.0),
            "importance": clip(importance, 0.0, 1.0),
            "frequency": clip(frequency, 0.0, 1.0),
            "relevance": clip(relevance, 0.0, 1.0),
        }

    def _activation(self, item: MemoryItem, cue_chars: Set[str], cue_bigrams: Set[str], now: float) -> float:
        parts = self.activation_components(item, cue_chars, cue_bigrams, now)
        weights = self.cfg.memory_activation_weights
        total = sum(weights)
        if total <= 0:
            return 0.0
        return sum(w * parts[k] for w, k in zip(weights, ("recency", "importance", "frequency", "relevance"))) / total

    def write(self, text: str, importance: float, rpe: float, now: float, valence: float = 0.0, arousal: float = 0.5) -> bool:
        if not text:
            return False
        p = sigmoid(2.0 * (importance - 0.3) + 0.8 * rpe)
        if self.rng.random() > p:
            return False
        self.items.append(MemoryItem(text=text, importance=clip(importance, 0.0, 1.0), t=now,
                                     valence=clip(float(valence), -1.0, 1.0), arousal=clip(float(arousal), 0.0, 1.0)))
        if len(self.items) > self.cfg.memory_capacity:
            self.consolidate(now)
        return True

    def _rank(self, cue: str, now: float) -> List[tuple]:
        if not cue or not self.items:
            return []
        cc = set(cue)
        cb = {cue[i:i + 2] for i in range(max(0, len(cue) - 1))}
        scored = []
        for item in self.items:
            score = self._activation(item, cc, cb, now)
            if self.affect_weight > 0.0:
                dv = abs(float(item.valence) - self.current_valence) / 2.0
                da = abs(float(item.arousal) - self.current_arousal)
                congruence = 1.0 - min(1.0, 0.6 * dv + 0.4 * da)
                score += self.affect_weight * congruence
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def retrieve_top_k(self, cue: str, now: float, k: int = 3, threshold: float = 0.05) -> List[MemoryItem]:
        ranked = [(a, i) for a, i in self._rank(cue, now) if a > threshold]
        out: List[MemoryItem] = []
        for _, item in ranked[: max(1, int(k))]:
            item.hits += 1
            item.last_access = now
            out.append(item)
        return out

    def retrieve_embedding(self, cue: str, now: float, k: int = 3, client=None, chunk_chars: int = 0) -> List[MemoryItem]:
        from .embedding import chunk_text, cosine

        if not cue or not self.items or client is None:
            return []
        q = client.embed([cue])[0]
        docs = []
        for item in self.items:
            parts = chunk_text(item.text, chunk_chars) if chunk_chars else [item.text]
            vecs = client.embed(parts)
            docs.append(max(vecs, key=lambda v: cosine(q, v)))
        order = sorted(range(len(docs)), key=lambda i: cosine(q, docs[i]), reverse=True)[:k]
        out: List[MemoryItem] = []
        for idx in order:
            item = self.items[idx]
            item.hits += 1
            item.last_access = now
            out.append(item)
        return out

    def retrieve_hybrid(self, cue: str, now: float, k: int = 3, client=None, alpha: float = 0.6) -> List[MemoryItem]:
        from .bm25 import BM25
        from .embedding import cosine

        if not cue or not self.items or client is None:
            return []
        docs = [i.text for i in self.items]
        bm = [BM25(docs).score(cue, i) for i in range(len(docs))]
        q = client.embed([cue])[0]
        emb = [cosine(q, v) for v in client.embed(docs)]
        def norm(xs):
            lo, hi = min(xs), max(xs)
            return [0.0] * len(xs) if hi <= lo else [(x - lo) / (hi - lo) for x in xs]
        bn, en = norm(bm), norm(emb)
        scores = [alpha * e + (1 - alpha) * b for b, e in zip(bn, en)]
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        out: List[MemoryItem] = []
        for idx in order:
            item = self.items[idx]
            item.hits += 1
            item.last_access = now
            out.append(item)
        return out

    def retrieve_bm25(self, cue: str, now: float, k: int = 3) -> List[MemoryItem]:
        from .bm25 import BM25

        if not cue or not self.items:
            return []
        ranked = BM25([i.text for i in self.items]).rank(cue, k=k)
        out: List[MemoryItem] = []
        for idx in ranked:
            item = self.items[idx]
            item.hits += 1
            item.last_access = now
            out.append(item)
        return out

    def retrieve(self, cue: str, now: float) -> Optional[MemoryItem]:
        if getattr(self.cfg, "memory_retrieval", "bm25") == "bm25":
            items = self.retrieve_bm25(cue, now, k=1)
            return items[0] if items else None
        ranked = self._rank(cue, now)
        if not ranked or ranked[0][0] <= 0.05:
            return None
        item = ranked[0][1]
        item.hits += 1
        item.last_access = now
        return item

    def consolidate(self, now: float) -> None:
        for item in self.items:
            item.importance = clip(item.importance * 0.995, 0.0, 1.0)
        self.items.sort(key=lambda i: i.importance, reverse=True)
        self.items = self.items[: self.cfg.memory_capacity]

    def snapshot(self) -> List[Dict[str, object]]:
        return [
            {
                "text": i.text,
                "importance": round(i.importance, 3),
                "hits": i.hits,
                "t": round(i.t, 1),
                "last_access": round(i.last_access, 1),
            }
            for i in self.items
        ]
