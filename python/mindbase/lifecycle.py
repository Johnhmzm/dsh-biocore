"""Lifespan, aging, sleep debt, and offline consolidation.

This is the original base Lifecycle: it ages, accumulates structural sleep
debt, applies age-related learning attenuation, and consolidates on sleep.
"""
from __future__ import annotations

import math
import random
from typing import Dict


class Lifecycle:
    def __init__(self, cfg, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.age = 0.0
        self.stage = "developing"
        self.age_factor = 1.0
        self.sleep_debt = 0.0

    def tick(self, dt: float) -> None:
        dt = max(0.0, float(dt))
        self.age += dt
        days = self.age / 86400.0
        if days < 7.0:
            self.stage = "developing"
        elif days < 30.0:
            self.stage = "young"
        elif days < 365.0:
            self.stage = "aging"
        else:
            self.stage = "old"
        if days < 30.0:
            self.age_factor = 1.0
        elif days < 365.0:
            self.age_factor = max(0.70, 1.0 - 0.01 - 0.25 * (days - 30.0) / 335.0)
        else:
            self.age_factor = max(0.35, 0.74 - 0.40 * min(1.0, (days - 365.0) / 365.0))

    def apply(self, mind) -> None:
        try:
            mind.learner.reinforcement_scale = max(0.2, min(1.0, float(self.age_factor)))
        except Exception:
            pass

    def add_structural_load(self, util: float, dt: float) -> None:
        hours = max(0.0, float(dt)) / 3600.0
        self.sleep_debt = min(2.0, self.sleep_debt + 0.05 * max(0.0, float(util)) * hours)

    def sleep(self, mind, duration: float = 3600.0) -> None:
        hours = max(0.0, float(duration)) / 3600.0
        self.sleep_debt = max(0.0, self.sleep_debt * math.exp(-hours / 2.0))
        try:
            mind.affect.stress = max(0.0, mind.affect.stress * max(0.0, 1.0 - 0.35 * min(1.0, hours)))
            mind.affect.scar = max(0.0, mind.affect.scar * max(0.0, 1.0 - 0.25 * min(1.0, hours)))
            mind.affect.fatigue = max(0.0, mind.affect.fatigue * max(0.0, 1.0 - 0.45 * min(1.0, hours)))
        except Exception:
            pass
        try:
            mind.will.willpower = min(1.0, mind.will.willpower + 0.35 * min(1.0, hours))
            mind.drives.values["energy"] = min(1.0, mind.drives.values.get("energy", 0.0) + 0.05 * min(1.0, hours))
        except Exception:
            pass
        try:
            for region in list(mind.circuit.lesions):
                mind.circuit.recover(region)
        except Exception:
            pass
        try:
            if hasattr(mind.learner, "consolidate"):
                mind.learner.consolidate()
        except Exception:
            pass
        try:
            if hasattr(mind.memory, "systems_consolidation"):
                mind.memory.systems_consolidation(mind.t)
        except Exception:
            pass

    def snapshot(self) -> Dict[str, float]:
        return {
            "age_days": round(self.age / 86400.0, 3),
            "stage": self.stage,
            "age_factor": round(self.age_factor, 3),
            "sleep_debt": round(self.sleep_debt, 3),
        }
