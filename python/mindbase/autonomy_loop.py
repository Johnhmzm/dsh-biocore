from __future__ import annotations

from collections import Counter
from typing import Dict

from .types import Event


class AutonomyLoop:
    """One loop over structure, allostasis, sleep, attachment and memory."""

    def __init__(self, mind):
        self.mind = mind

    def run_with_messages(self, messages, ticks: int = 300, dt: float = 3600.0, every: int = 10) -> Dict[str, object]:
        actions, reasons = Counter(), Counter()
        viab, stress = [], []
        msgs = list(messages)
        mi = 0
        for i in range(ticks):
            if i % every == 0 and mi < len(msgs):
                out = self.mind.step(Event(text=str(msgs[mi]), speaker="A", directed=True, ts=self.mind.t + dt))
                self.mind.post_reply("嗯", feedback=0.2, speaker="A")
                mi += 1
            else:
                out = self.mind.step(Event(kind="tick", ts=self.mind.t + dt))
            actions[out.action] += 1
            reasons[out.reason] += 1
            viab.append(self.mind.viability_state["viability"])
            stress.append(self.mind.affect.stress)
        return {
            "ticks": ticks, "messages": mi, "actions": dict(actions), "reasons": dict(reasons),
            "viability_min": round(min(viab), 3) if viab else 1.0,
            "stress_max": round(max(stress), 3) if stress else 0.0,
            "fatigue": round(self.mind.affect.fatigue, 3),
            "scar": round(self.mind.circuit.chronic_scar, 3),
            "sleep_debt": round(self.mind.lifecycle.sleep_debt, 3),
            "identity": round(self.mind.self_model.identity, 3),
            "attachment": self.mind.attachment.style(),
            "memory": self.mind.memory.snapshot(),
        }

    def run(self, ticks: int = 300, dt: float = 3600.0) -> Dict[str, object]:
        actions, reasons = Counter(), Counter()
        viab, stress = [], []
        for _ in range(ticks):
            out = self.mind.step(Event(kind="tick", ts=self.mind.t + dt))
            actions[out.action] += 1
            reasons[out.reason] += 1
            viab.append(self.mind.viability_state["viability"])
            stress.append(self.mind.affect.stress)
        return {
            "ticks": ticks,
            "actions": dict(actions),
            "reasons": dict(reasons),
            "viability_min": round(min(viab), 3) if viab else 1.0,
            "stress_max": round(max(stress), 3) if stress else 0.0,
            "fatigue": round(self.mind.affect.fatigue, 3),
            "scar": round(self.mind.circuit.chronic_scar, 3),
            "sleep_debt": round(self.mind.lifecycle.sleep_debt, 3),
            "identity": round(self.mind.self_model.identity, 3),
            "attachment": self.mind.attachment.style(),
            "memory": self.mind.memory.snapshot(),
            "circuit": {"sparsity": round(self.mind.circuit.sparsity, 3), "lesions": sorted(self.mind.circuit.lesions)},
        }
