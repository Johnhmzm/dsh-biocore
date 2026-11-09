"""Simple turn-level observability metrics from turns.jsonl."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


def _tokens(s: str) -> set:
    return set(str(s or "").lower().split())


def jaccard(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def summarize(rows: List[dict]) -> Dict[str, object]:
    total = len(rows)
    replies = [str(r.get("reply") or "") for r in rows]
    nonempty = [r for r in replies if r]
    gated = sum(1 for r in rows if r.get("gated"))
    proactive = sum(1 for r in rows if r.get("proactive"))
    group_auto = sum(1 for r in rows if r.get("group_auto"))
    repeats = 0
    for a, b in zip(replies, replies[1:]):
        if a and b and jaccard(a, b) >= 0.55:
            repeats += 1
    return {
        "turns": total,
        "replied": len(nonempty),
        "empty": total - len(nonempty),
        "gated": gated,
        "proactive": proactive,
        "group_auto": group_auto,
        "avg_reply_chars": round(sum(len(x) for x in nonempty) / max(1, len(nonempty)), 1),
        "repeat_pairs": repeats,
        "repeat_rate": round(repeats / max(1, total - 1), 3),
    }


def summarize_file(path: Path | str) -> Dict[str, object]:
    p = Path(path)
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return summarize(rows)
