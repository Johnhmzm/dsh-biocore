"""Global uncertainty / chaos field.

Everything can see the same uncertainty state: physiology, affect, needs,
action policy, memory and evolution.  Each component has a mutable gain; gain=0
keeps old deterministic behaviour.

Two sources:
  - logistic chaos x -> temporal instability
  - QuantumUncertainty density matrix -> entropy / coherence
"""
from __future__ import annotations

import random
from typing import Dict

from .noise import clip
from .quantum import QuantumUncertainty


class UncertaintyField:
    def __init__(self, cfg, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.x = 0.5
        self.r = float(getattr(cfg, "chaos_r", 3.9))
        self.q = QuantumUncertainty.from_affect(0.0, 0.5, 0.3)
        self.last_entropy = self.q.entropy()
        self.gains: Dict[str, float] = {
            "physiology": float(getattr(cfg, "uncertainty_gain", 0.0)),
            "affect": float(getattr(cfg, "uncertainty_gain", 0.0)),
            "needs": float(getattr(cfg, "uncertainty_gain", 0.0)),
            "action": float(getattr(cfg, "uncertainty_gain", 0.0)),
            "memory": float(getattr(cfg, "uncertainty_gain", 0.0)),
        }

    def update(self, dt: float = 1.0, load: float = 0.0) -> float:
        self.x = max(0.0, min(1.0, self.r * self.x * (1.0 - self.x)))
        # one fixed-size quantum update per event; avoid O(dt) loops on long ticks
        self.q.step(1.0, gamma=0.05, dephase=0.10)
        self.last_entropy = self.q.entropy()
        return self.last_entropy

    def jitter(self, kind: str, scale: float = 1.0) -> float:
        g = float(self.gains.get(kind, 0.0))
        if g <= 0.0:
            return 0.0
        return g * scale * (self.rng.gauss(0.0, 0.5) * (0.4 + self.last_entropy) + 0.2 * (self.x - 0.5))

    def apply(self, value: float, kind: str, scale: float = 1.0,
              lo: float | None = None, hi: float | None = None) -> float:
        out = float(value) + self.jitter(kind, scale)
        if lo is not None:
            out = max(float(lo), out)
        if hi is not None:
            out = min(float(hi), out)
        return out

    def snapshot(self) -> Dict[str, float]:
        return {"chaos_x": round(self.x, 4), "entropy": round(self.last_entropy, 4),
                "purity": round(self.q.purity(), 4), "coherence": round(self.q.coherence(), 4),
                "gains": dict(self.gains)}
