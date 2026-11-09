from __future__ import annotations

from typing import Dict

from .noise import clip


class Attachment:
    """Two-axis attachment: anxiety (fear of abandonment) and avoidance (distance)."""

    def __init__(self):
        self.trait_anxiety = 0.30
        self.trait_avoidance = 0.30
        self.anxiety = 0.30
        self.avoidance = 0.30
        self.lr = 0.02

    def update(self, social_signal: float, prediction_error: float, dt: float, fatigue: float = 0.0, scar: float = 0.0) -> None:
        sig = float(social_signal)
        pe = abs(float(prediction_error))
        rate = min(1.0, max(0.0, dt) / 600.0)
        # negative social signal and unpredictable care raise anxiety
        self.anxiety = clip(self.anxiety + self.lr * rate * (max(0.0, -sig) + 0.20 * pe + 0.10 * float(fatigue) + 0.10 * float(scar)), 0.0, 1.0)
        # repeated negative/unpredictable care also raises avoidance
        self.avoidance = clip(self.avoidance + self.lr * rate * (max(0.0, -sig) + 0.10 * pe + 0.08 * float(fatigue) + 0.15 * float(scar)), 0.0, 1.0)
        # positive, predictable care slowly repairs both
        if sig > 0:
            self.anxiety = clip(self.anxiety - self.lr * rate * sig, self.trait_anxiety, 1.0)
            self.avoidance = clip(self.avoidance - self.lr * rate * sig, self.trait_avoidance, 1.0)

    def action_kind(self) -> str:
        return {"secure": "communicate", "anxious": "seek_help", "avoidant": "avoid", "fearful": "protect"}[self.style()]

    def memory_bias(self) -> float:
        # anxious attachment prioritizes relational/threat cues
        return 1.0 + 0.30 * float(self.anxiety)

    def initiation_bias(self) -> float:
        return {"secure": 1.0, "anxious": 1.20, "avoidant": 0.70, "fearful": 0.85}[self.style()]

    def style(self) -> str:
        a, v = self.anxiety >= 0.55, self.avoidance >= 0.55
        if a and v: return "fearful"
        if a: return "anxious"
        if v: return "avoidant"
        return "secure"

    def snapshot(self) -> Dict[str, object]:
        return {"anxiety": round(self.anxiety, 3), "avoidance": round(self.avoidance, 3), "style": self.style()}
