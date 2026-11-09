from __future__ import annotations

import math
from typing import Dict, List

from .config import MindConfig


class WorkingMemory:
    """Limited-capacity slots with decay. Items are plain strings for now."""

    def __init__(self, cfg: MindConfig):
        self.cfg = cfg
        self.slots: List[Dict[str, object]] = []

    def push(self, item: str, salience: float, t: float) -> None:
        if not item:
            return
        self.slots.append({"item": item, "salience": float(salience), "t": t})
        self.slots.sort(key=lambda s: float(s["salience"]), reverse=True)
        self.slots = self.slots[: self.cfg.wm_capacity]

    def tick(self, dt: float, t: float) -> None:
        for s in self.slots:
            s["salience"] = float(s["salience"]) * math.exp(-dt / max(self.cfg.wm_decay_tau, 1e-6))
        self.slots = [s for s in self.slots if float(s["salience"]) > 0.05]

    def snapshot(self) -> List[Dict[str, object]]:
        return [
            {"item": s["item"], "salience": round(float(s["salience"]), 3), "t": round(float(s["t"]), 1)}
            for s in self.slots
        ]
