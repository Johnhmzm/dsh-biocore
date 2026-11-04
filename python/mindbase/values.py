from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .noise import clip


@dataclass
class Value:
    name: str
    weight: float


class ValueSystem:
    """A small ranked value system. Values shape which goals become intentions."""

    NAMES = ("care", "curiosity", "order", "autonomy", "safety", "honesty")

    def __init__(self, values: Optional[Dict[str, float]] = None):
        base = values or {n: 0.5 for n in self.NAMES}
        self.values: Dict[str, float] = {n: clip(float(base.get(n, 0.5)), 0.0, 1.0) for n in self.NAMES}

    @classmethod
    def from_persona(cls, persona) -> "ValueSystem":
        # personality traits -> value weights (rough, tunable)
        return cls(
            {
                "care": persona.A,
                "curiosity": persona.O,
                "order": persona.C,
                "autonomy": clip(0.5 + 0.5 * (persona.E - 0.5) + 0.2 * (1.0 - persona.A), 0.0, 1.0),
                "safety": clip(1.0 - persona.N, 0.0, 1.0),
                "honesty": 0.60,
            }
        )

    def weight(self, name: str) -> float:
        return self.values.get(name, 0.5)

    def top(self) -> Value:
        name = max(self.values, key=lambda k: self.values[k])
        return Value(name=name, weight=self.values[name])

    def align(self, action: str, goal) -> float:
        if goal is None:
            return 0.5
        w = self.weight(getattr(goal, "value", "honesty"))
        return w if action == getattr(goal, "action", "speak") else 1.0 - w

    def drift(self, evidence: Dict[str, float], lr: float = 0.02) -> Dict[str, float]:
        """Slowly move value weights by evidence. Returns actual deltas."""
        deltas: Dict[str, float] = {}
        for name, delta in evidence.items():
            if name not in self.values:
                continue
            old = self.values[name]
            self.values[name] = clip(old + lr * delta, 0.0, 1.0)
            change = self.values[name] - old
            if abs(change) > 1e-9:
                deltas[name] = round(change, 5)
        return deltas

    def observe_feedback(
        self,
        valence: float,
        source: str = "user",
        relationship: str = "stranger",
        goal=None,
    ) -> Dict[str, float]:
        """Positive feedback grows care/honesty; negative grows safety/order."""
        source_weight = {"user": 1.0, "llm": 0.5, "self": 0.35}.get(source, 0.5)
        v = clip(float(valence), -1.0, 1.0) * source_weight
        evidence: Dict[str, float] = {}
        if v > 0:
            evidence["care"] = 0.60 * v
            evidence["honesty"] = 0.30 * v
            evidence["safety"] = -0.10 * v
            if relationship in ("familiar", "trusted", "intimate"):
                evidence["care"] = evidence.get("care", 0.0) + 0.10 * v
            if goal is not None:
                evidence[goal.value] = evidence.get(goal.value, 0.0) + 0.40 * v
        elif v < 0:
            a = abs(v)
            evidence["safety"] = 0.80 * a
            evidence["order"] = 0.30 * a
            evidence["care"] = -0.20 * a
            if goal is not None:
                evidence[goal.value] = evidence.get(goal.value, 0.0) - 0.10 * a
        return self.drift(evidence)

    def observe_goal(self, goal, success: bool = True) -> Dict[str, float]:
        if goal is None:
            return {}
        if success:
            evidence = {goal.value: 0.60, "order": 0.20}
        else:
            evidence = {goal.value: -0.30, "safety": 0.30}
        return self.drift(evidence)

    def snapshot(self) -> Dict[str, float]:
        return {k: round(v, 3) for k, v in self.values.items()}
