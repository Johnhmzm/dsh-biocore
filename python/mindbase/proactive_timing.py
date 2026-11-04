from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ProactiveDecision:
    value: float
    cost: float
    score: float
    speak: bool


class ProactiveTiming:
    """Decision-theoretic proactive timing shadow. No message is sent."""

    def __init__(self, threshold: float = 0.05):
        self.threshold = float(threshold)

    def decide(self, relevance: float, novelty: float, idle_seconds: float, stress: float) -> ProactiveDecision:
        relevance = max(0.0, min(1.0, float(relevance)))
        novelty = max(0.0, min(1.0, float(novelty)))
        stress = max(0.0, min(1.0, float(stress)))
        value = 0.6 * relevance + 0.4 * novelty
        cost = 0.5 * math.exp(-max(0.0, float(idle_seconds)) / 600.0) + 0.3 * stress + 0.1
        score = value - cost
        return ProactiveDecision(value=round(value, 4), cost=round(cost, 4), score=round(score, 4), speak=score > self.threshold)
