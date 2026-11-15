from __future__ import annotations

from collections import Counter
from typing import Dict

from ..config import MindConfig
from ..engine import MindBase
from ..types import Event
from .behavior import load_annotations
from .behavior_advice import BehaviorAdvice


def run(limit: int = 100, seed: int = 42, threshold: float = 0.0) -> Dict[str, object]:
    samples = [s for s in load_annotations(limit=limit) if s.action_kind]
    advice = BehaviorAdvice()
    mb = MindBase(MindConfig(seed=seed), heads={"action_advice": advice})
    actions = Counter()
    acts = Counter()
    hits = 0
    for i, s in enumerate(samples):
        out = mb.step(Event(text=s.event, speaker="A", directed=False, ts=mb.t + 60, domain="embodied"))
        advice_kind = (out.prompt_state.get("action_advice") or {}).get("kind")
        hits += int(advice_kind == s.action_kind)
        actions[out.action] += 1
        acts[out.act.kind if out.act else "none"] += 1
    return {
        "n": len(samples),
        "advice_accuracy": round(hits / max(1, len(samples)), 4),
        "actions": dict(actions),
        "acts": dict(acts),
        "identity": round(mb.self_model.identity, 3),
        "stress": round(mb.affect.stress, 3),
        "health": mb.health()["ok"],
    }


if __name__ == "__main__":
    print(run())
