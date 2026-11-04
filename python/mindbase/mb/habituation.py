from __future__ import annotations

import math
from typing import Dict

from ..noise import clip


class Habituation:
    """Non-associative learning: repeated cue -> weaker response; threat -> sensitize."""

    def __init__(self, tau: float = 300.0):
        self.tau = tau
        self.state: Dict[str, float] = {}

    def response(self, cue: str) -> float:
        return self.state.get(cue, 1.0)

    def on_cue(self, cue: str, valence: float, dt: float) -> float:
        if not cue:
            return 1.0
        h = self.state.get(cue, 1.0)
        h *= math.exp(-max(0.0, dt) / max(self.tau, 1e-6))
        if valence < -0.3:
            h = clip(h + 0.30, 0.0, 1.0)  # sensitization after negative event
        self.state[cue] = clip(h, 0.0, 1.0)
        return self.state[cue]
