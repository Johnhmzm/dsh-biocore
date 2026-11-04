from __future__ import annotations

from typing import Dict

from .config import MindConfig
from .noise import clip, sigmoid
from .types import Appraisal


class Meta:
    """Confidence / uncertainty / abstention (operational metacognition)."""

    def __init__(self, cfg: MindConfig):
        self.cfg = cfg
        self.confidence = 0.50
        self.uncertainty = 0.50

    def update(self, ap: Appraisal, memory_hit: bool, stress: float) -> None:
        evidence = 1.0 if memory_hit else 0.40
        unc = clip(0.50 * ap.novelty + 0.30 * (1.0 - ap.control) + 0.20 * stress, 0.0, 1.0)
        self.uncertainty = unc
        self.confidence = sigmoid(2.5 * (evidence - unc))

    def should_abstain(self) -> bool:
        return self.confidence < self.cfg.abstain_threshold

    def snapshot(self) -> Dict[str, float]:
        return {
            "confidence": round(self.confidence, 3),
            "uncertainty": round(self.uncertainty, 3),
            "abstain": self.should_abstain(),
        }
