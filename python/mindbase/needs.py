"""Human need pyramid (Maslow-style), mutable and continuous.

Levels:
  physiological -> safety -> belonging -> esteem -> actualization

Lower-level deficits gate higher-level priorities.  Weights and the gate are
parameters (mutable/evolvable), not behavioural rules.
"""
from __future__ import annotations

from typing import Dict

from .noise import clip


LEVELS = ("physiological", "safety", "belonging", "esteem", "actualization")


class NeedPyramid:
    def __init__(self, cfg=None):
        self.cfg = cfg
        self.weights: Dict[str, float] = {
            "physiological": 1.00,
            "safety": 0.85,
            "belonging": 0.65,
            "esteem": 0.50,
            "actualization": 0.35,
        }
        self.step_gate = 0.75
        self.deficits: Dict[str, float] = {k: 0.0 for k in LEVELS}
        self.scores: Dict[str, float] = {k: 0.0 for k in LEVELS}

    def update(self, energy: float, hydration: float, sleep_pressure: float,
               pain: float, inflammation: float, viability: float, stress: float,
               social: float, identity: float, confidence: float,
               curiosity: float, novelty: float, mode: str = "survival") -> None:
        phys = 1.0 - min(clip(energy, 0.0, 1.0), clip(hydration, 0.0, 1.0),
                         clip(1.0 - sleep_pressure, 0.0, 1.0),
                         clip(1.0 - pain, 0.0, 1.0), clip(1.0 - inflammation, 0.0, 1.0))
        safety = clip(0.6 * (1.0 - float(viability)) + 0.4 * float(stress), 0.0, 1.0)
        if str(mode) == "chat":
            # conversation is a safe/satiated context; do not let raw survival
            # deficits lock every chat turn at physiological level
            phys = min(phys, 0.12)
            safety = min(safety, 0.12)
        belonging = clip(1.0 - float(social), 0.0, 1.0)
        esteem = clip(1.0 - 0.5 * float(identity) - 0.5 * float(confidence), 0.0, 1.0)
        actual = clip(1.0 - 0.6 * float(curiosity) - 0.4 * float(novelty), 0.0, 1.0)
        self.last_mode = str(mode)
        self.deficits = {
            "physiological": round(phys, 4), "safety": round(safety, 4),
            "belonging": round(belonging, 4), "esteem": round(esteem, 4),
            "actualization": round(actual, 4),
        }
        raw = {k: self.deficits[k] * self.weights[k] for k in LEVELS}
        # stepwise gating: a large lower deficit suppresses higher priorities
        out = {}
        lower_max = 0.0
        for k in LEVELS:
            gate = 1.0 - self.step_gate * lower_max
            out[k] = round(clip(raw[k] * gate, 0.0, 1.0), 4)
            lower_max = max(lower_max, self.deficits[k])
        self.scores = out

    def dominant(self) -> str:
        for k in LEVELS:
            if self.deficits.get(k, 0.0) >= 0.25:
                return k
        return max(LEVELS, key=lambda k: self.scores.get(k, 0.0))

    def satisfaction(self) -> float:
        return round(1.0 - max(self.deficits.values() or [0.0]), 4)

    def snapshot(self) -> Dict[str, object]:
        return {"deficits": dict(self.deficits), "scores": dict(self.scores),
                "dominant": self.dominant(), "satisfaction": self.satisfaction(),
                "mode": str(getattr(self, "last_mode", "survival"))}
