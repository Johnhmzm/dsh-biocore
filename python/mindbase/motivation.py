"""Numeric motivation state (self-determination-inspired), no LLM call."""
from __future__ import annotations


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


class MotivationState:
    def __init__(self):
        self.autonomy = 0.6
        self.competence = 0.5
        self.relatedness = 0.5
        self.intrinsic = 0.5

    def update(self, connection: float = 0.0, constraints: int = 0,
               energy: float = 0.5, curiosity: float = 0.5,
               trust: float = 0.5) -> float:
        self.autonomy = _clip(1.0 - 0.18 * max(0, int(constraints)))
        self.competence = _clip(0.5 * _clip(energy) + 0.3 * _clip(trust) + 0.2 * _clip(1.0 - connection))
        self.relatedness = _clip(0.6 * _clip(1.0 - connection) + 0.4 * _clip(trust))
        self.intrinsic = _clip(0.35 * _clip(curiosity) + 0.25 * self.competence +
                               0.20 * self.autonomy + 0.20 * self.relatedness)
        return round(self.intrinsic, 4)

    def context(self) -> str:
        return ("自主=%.2f 胜任=%.2f 联结=%.2f 内在动机=%.2f"
                % (self.autonomy, self.competence, self.relatedness, self.intrinsic))
