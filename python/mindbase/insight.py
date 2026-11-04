from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Optional

from .noise import jaccard


class InsightEngine:
    """Minimal insight: recombine distant memories, score novelty and relevance."""

    def __init__(self, memory):
        self.memory = memory

    def generate(self, cue: str, now: float, k: int = 6, criticality: float = 0.0) -> Optional[Dict[str, object]]:
        items = self.memory.retrieve_top_k(cue, now, k=k)
        items = [x for x in items if len(str(x.text or "").strip()) >= 4]
        if len(items) < 2:
            return None
        best = None
        candidates = []
        for a, b in combinations(items, 2):
            distance = 1.0 - jaccard(a.bigrams, b.bigrams)
            if distance < 0.5:
                continue
            # recombine: first half of one memory + second half of the other
            left, right = a.text[: max(1, len(a.text) // 2)], b.text[len(b.text) // 2 :]
            candidate = (left + right).strip(" ，。,.!?！？")
            if len(candidate) < 5 or candidate in (a.text, b.text):
                continue
            if max(jaccard({candidate[i:i + 2] for i in range(max(0, len(candidate) - 1))}, x.bigrams) for x in items) >= 0.92:
                continue
            cand_chars = set(candidate)
            novelty = 1.0 - max(jaccard({candidate[i:i + 2] for i in range(max(0, len(candidate) - 1))}, x.bigrams) for x in items)
            relevance = jaccard(cand_chars, set(cue)) if cue else 0.0
            coherence = 0.5 * (jaccard(cand_chars, set(a.text)) + jaccard(cand_chars, set(b.text)))
            score = 0.40 * novelty + 0.40 * relevance + 0.20 * coherence + 0.10 * max(0.0, float(criticality))
            row = {"candidate": candidate, "score": round(score, 3), "criticality": round(float(criticality), 3), "distance": round(distance, 3), "novelty": round(novelty, 3), "relevance": round(relevance, 3), "coherence": round(coherence, 3)}
            candidates.append(row)
            if score < 0.10:
                continue
            if best is None or score > best["score"]:
                best = row
        candidates.sort(key=lambda x: x["score"], reverse=True)
        self.last_candidates = candidates
        return best

    def generate_many(self, cue: str, now: float, k: int = 6, criticality: float = 0.0, max_n: int = 8):
        self.generate(cue, now, k, criticality)
        n = min(max_n, 1 + int(round(4.0 * max(0.0, float(criticality)))))
        return list(getattr(self, "last_candidates", []))[:n]
