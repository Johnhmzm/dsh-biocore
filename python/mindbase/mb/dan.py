from __future__ import annotations

from ..noise import clip


class DAN:
    """Dopaminergic teaching signal: reward minus prediction (RPE)."""

    def __init__(self):
        self.last = 0.0

    def compute(self, reward: float, prediction: float) -> float:
        self.last = clip(reward - prediction, -1.0, 1.0)
        return self.last
