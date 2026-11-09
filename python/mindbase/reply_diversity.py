"""Cheap reply-diversity metrics and near-duplicate detection."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Sequence


def bigrams(text: str) -> set:
    t = re.sub(r"\s+", "", str(text or ""))
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = bigrams(a), bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def near_duplicate(reply: str, recent: Sequence[str], threshold: float = 0.62) -> float:
    return max((similarity(reply, r) for r in recent), default=0.0) >= threshold


def _opening(text: str) -> str:
    t = re.sub(r"\s+", "", str(text or ""))
    return t[:4]


def stats(replies: Sequence[str]) -> Dict[str, object]:
    rows = [str(r or "") for r in replies if str(r or "").strip()]
    n = len(rows)
    if not n:
        return {"n": 0, "unique_ratio": 0.0, "near_dup_ratio": 0.0,
                "mean_pair_similarity": 0.0, "opening_entropy": 0.0}
    unique = len(set(rows))
    near = 0
    sims = []
    for i, r in enumerate(rows):
        prev = rows[:i]
        if prev:
            s = max(similarity(r, p) for p in prev)
            sims.append(s)
            near += int(s >= 0.62)
    open_counts = Counter(_opening(r) for r in rows)
    probs = [c / n for c in open_counts.values()]
    entropy = -sum(p * math.log(p + 1e-12) for p in probs) / math.log(max(2, len(open_counts)))
    return {"n": n,
            "unique_ratio": round(unique / n, 3),
            "near_dup_ratio": round(near / max(1, n - 1), 3),
            "mean_pair_similarity": round(sum(sims) / max(1, len(sims)), 3),
            "opening_entropy": round(entropy, 3)}
