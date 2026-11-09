from __future__ import annotations

from typing import List

from ..noise import clip


class APL:
    """Feedback inhibition loop: KC activity -> APL -> inhibition of KC output."""

    def __init__(self, strength: float = 0.50, tau: float = 0.20):
        self.strength = strength
        self.tau = tau
        self.activity = 0.0

    def step(self, kc: List[int], n_kc: int, dt: float = 0.05) -> float:
        if n_kc <= 0:
            return 0.0
        density = len(kc) / float(n_kc)
        self.activity += (density - self.activity) * (max(0.0, dt) / max(self.tau, 1e-6))
        self.activity = clip(self.activity, 0.0, 1.0)
        return 1.0 / (1.0 + self.strength * self.activity * n_kc)
