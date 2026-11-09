from __future__ import annotations

from collections import Counter
from typing import Dict, List

from .types import Event


class UserLoop:
    """User-centered loop: dialogue, gaps, proactive care, relationship and memory."""

    def __init__(self, mind):
        self.mind = mind

    def run(self, messages: List[str], gap_ticks: int = 3, dt: float = 600.0) -> Dict[str, object]:
        actions, reasons = Counter(), Counter()
        proactive = 0
        replied = 0
        for msg in messages:
            out = self.mind.step(Event(text=str(msg), speaker="A", directed=True, ts=self.mind.t + dt))
            self.mind.post_reply("嗯", feedback=0.2, speaker="A")
            actions[out.action] += 1
            reasons[out.reason] += 1
            if out.action == "speak":
                replied += 1
            for _ in range(gap_ticks):
                g = self.mind.step(Event(kind="tick", ts=self.mind.t + dt))
                actions[g.action] += 1
                reasons[g.reason] += 1
                if g.action == "speak":
                    proactive += 1
        return {
            "messages": len(messages),
            "replied": replied,
            "proactive": proactive,
            "actions": dict(actions),
            "reasons": dict(reasons),
            "trust": round(self.mind.social.trust_for("A"), 3),
            "stage": self.mind.social.stage("A"),
            "attachment": self.mind.attachment.style(),
            "identity": round(self.mind.self_model.identity, 3),
            "memory": self.mind.memory.snapshot(),
            "scar": round(self.mind.circuit.chronic_scar, 3),
            "fatigue": round(self.mind.affect.fatigue, 3),
        }
