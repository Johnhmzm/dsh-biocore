"""Multi-channel chaotic noise for the bio layer.

Not physics. A Lorenz field drives several bounded OU channels; each channel
has a different time constant and gain, so stress/energy/social/attention
perturbations arrive out of phase instead of as one random number.
"""
from __future__ import annotations

import random

from .noise import clip
from .quantum_chaos import ChaosField

CHANNELS = ("stress", "energy", "social", "attention", "valence")


class BioNoise:
    def __init__(self, seed: int = 2026):
        self.field = ChaosField(seed)
        self.rng = random.Random(seed + 7)
        self.values = {k: 0.0 for k in CHANNELS}
        self.last_chaos = 0.5

    def step(self, dt: float = 1.0) -> dict:
        steps = max(1, min(40, int(abs(float(dt or 1.0)) * 10)))
        self.last_chaos = float(self.field.step(steps=steps).get("value", 0.5))
        for i, k in enumerate(CHANNELS):
            theta = 0.10 + 0.025 * i
            sigma = 0.018 + 0.004 * i
            x = float(self.values.get(k, 0.0))
            x += theta * (0.0 - x)
            x += sigma * (self.last_chaos - 0.5)
            x += sigma * (self.rng.random() - 0.5)
            self.values[k] = clip(x, -0.15, 0.15)
        return dict(self.values)

    def snapshot(self) -> dict:
        return {"values": {k: round(float(v), 4) for k, v in self.values.items()},
                "chaos": round(self.last_chaos, 4)}
