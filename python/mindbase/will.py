from __future__ import annotations

import random
from typing import Dict, Optional

from .config import MindConfig
from .intention import Intention
from .noise import clip


class Volition:
    """Impulse vs intention arbitration with a depletable willpower budget.

    This is an operational model of volition, not a claim about free will.
    """

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.willpower = 1.0
        self.conflict = 0.0
        self.last_source = "impulse"
        self.temperature = float(getattr(cfg, "volition_temperature", 0.0))

    def tick(self, dt: float, stress: float, energy: float, asleep: bool) -> None:
        recovery = (0.0008 if asleep else 0.0003) * dt
        drain = (0.0004 * stress + 0.0002 * (1.0 - energy)) * dt
        self.willpower = clip(self.willpower + recovery - drain, 0.0, 1.0)

    def choose(self, impulse: float, intention: Optional[Intention]) -> tuple[str, str]:
        impulse = clip(impulse, 0.0, 1.0)
        if intention is None:
            self.last_source = "impulse"
            return self._impulse_choice(impulse), "impulse"
        self.conflict = abs(impulse - intention.strength)
        override = self.willpower * (0.5 + 0.5 * intention.commitment)
        if override >= 0.45:
            self.last_source = "intention"
            return intention.action, "intention"
        self.last_source = "impulse"
        return self._impulse_choice(impulse), "impulse"

    def _impulse_choice(self, impulse: float) -> str:
        temp = max(0.0, float(self.temperature))
        if temp <= 1e-9:
            return "speak" if impulse >= 0.5 else "silence"
        p = 1.0 / (1.0 + __import__("math").exp(-(float(impulse) - 0.5) / max(0.02, temp)))
        return "speak" if self.rng.random() < p else "silence"

    def on_action(self, spoke: bool, source: str) -> None:
        cost = 0.03 if source == "intention" else 0.01
        self.willpower = clip(self.willpower - cost, 0.0, 1.0)

    def snapshot(self) -> Dict[str, float]:
        return {
            "willpower": round(self.willpower, 3),
            "conflict": round(self.conflict, 3),
            "last_source": self.last_source,
            "temperature": round(self.temperature, 4),
        }
