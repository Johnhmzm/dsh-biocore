from __future__ import annotations

import math
import random
from typing import Dict, List

from .config import MindConfig
from .noise import clip, ou


class PopulationClock:
    """Multi-scale leaky integrators (2h/12h/48h) + circadian + sleep pressure."""

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.scales: List[float] = [0.0] * len(cfg.clock_taus)
        self.sleep_pressure = 0.25
        self.t = 0.0
        self.phase = 0.0

    def tick(self, dt: float, activity: float = 0.0, asleep: bool = False) -> None:
        self.t += dt
        for i, tau in enumerate(self.cfg.clock_taus):
            theta = 1.0 / max(tau, 1e-6)
            self.scales[i] = ou(self.scales[i], activity, theta, 0.02, dt, self.rng)
        rate = 0.000004 * dt
        if asleep:
            self.sleep_pressure = clip(self.sleep_pressure - rate * 2.0, 0.0, 1.0)
        else:
            self.sleep_pressure = clip(self.sleep_pressure + rate, 0.0, 1.0)

    @property
    def drive(self) -> float:
        return sum(w * s for w, s in zip(self.cfg.clock_weights, self.scales))

    @property
    def circadian(self) -> float:
        # 24h cycle; 0.5 at t=0, low at night. Demo uses sim time.
        return 0.5 + 0.5 * math.sin(2.0 * math.pi * (self.t / 86400.0 - 0.25 + self.phase))

    @property
    def asleep(self) -> bool:
        return self.circadian < 0.22 and self.sleep_pressure > 0.65

    def snapshot(self) -> Dict[str, float]:
        return {
            "drive": round(self.drive, 4),
            "circadian": round(self.circadian, 4),
            "sleep_pressure": round(self.sleep_pressure, 4),
            "asleep": self.asleep,
            "scales": [round(x, 4) for x in self.scales],
        }
