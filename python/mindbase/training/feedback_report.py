from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

FEEDBACK = Path(__file__).resolve().parent.parent / "data" / "one_on_one_feedback.jsonl"
INTERACTIONS = Path(__file__).resolve().parent.parent / "data" / "one_on_one_interactions.jsonl"


def _load(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def report() -> dict:
    fb = _load(FEEDBACK)
    turns = _load(INTERACTIONS)
    labels = Counter(str(r.get("label")) for r in fb)
    return {
        "turns": len(turns),
        "feedback": len(fb),
        "labels": dict(labels),
        "silent_rate": round(labels.get("silent", 0) / len(fb), 3) if fb else 0.0,
        "good_rate": round(labels.get("good", 0) / len(fb), 3) if fb else 0.0,
        "coverage": round(len(fb) / len(turns), 3) if turns else 0.0,
    }


if __name__ == "__main__":
    print(report())
