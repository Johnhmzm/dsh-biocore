from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .interaction_log import InteractionLogger

DEFAULT = Path(__file__).resolve().parent / "data" / "one_on_one_interactions.jsonl"


def metrics(path: Path | str | None = None) -> Dict[str, object]:
    rows = InteractionLogger(path or DEFAULT).read()
    if not rows:
        return {"n": 0, "note": "no interactions"}
    actions = {}
    for r in rows:
        actions[str(r.get("action"))] = actions.get(str(r.get("action")), 0) + 1
    responded = sum(1 for r in rows if r.get("user_response"))
    return {
        "n": len(rows),
        "actions": actions,
        "user_response_rate": round(responded / len(rows), 3),
        "avg_latency": round(sum(float(r.get("latency") or 0) for r in rows) / len(rows), 2),
    }


def can_iterate(path: Path | str | None = None, minimum: int = 100) -> bool:
    return metrics(path).get("n", 0) >= minimum
