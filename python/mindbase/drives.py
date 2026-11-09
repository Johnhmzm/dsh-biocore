from __future__ import annotations

import random
from typing import Dict

from .config import MindConfig
from .noise import clip


class Drives:
    """Homeostatic drives: energy, social, safety, curiosity in [0, 1]."""

    NAMES = ("energy", "social", "safety", "curiosity")

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.values: Dict[str, float] = {
            "energy": 0.75,
            "social": 0.60,
            "safety": 0.80,
            "curiosity": 0.60,
        }
        # allostasis: setpoints are predictions, not fixed points
        # setpoints are estimated from experience, not fixed per-drive constants
        self.setpoints: Dict[str, float] = {k: 0.50 for k in self.values}
        self.errors: Dict[str, float] = {k: 0.0 for k in self.values}
        self.setpoint_ticks = 0
        self.calibrated = False
        # coarse neuromodulators: DA / 5-HT-like / OA-like
        self.modulators: Dict[str, float] = {"da": 0.50, "serotonin": 0.50, "octopamine": 0.50}

    def tick(self, dt: float, asleep: bool = False) -> None:
        # slow homeostatic drift (per second)
        if asleep:
            self.values["energy"] += 0.00002 * dt
            self.values["social"] -= 0.000005 * dt
        else:
            # social/curiosity are unmet needs: idle time raises them
            self.values["energy"] -= 0.000012 * dt
            self.values["social"] += 0.000010 * dt
            self.values["curiosity"] += 0.000004 * dt
        self.values["safety"] += 0.000004 * dt
        for k in self.NAMES:
            self.values[k] = clip(self.values[k], 0.0, 1.0)
            # estimate setpoint from observed demand (running mean first, EMA later)
            if not self.calibrated:
                n = max(1, self.setpoint_ticks + 1)
                self.setpoints[k] = clip(self.setpoints[k] + (self.values[k] - self.setpoints[k]) / n, 0.0, 1.0)
            else:
                self.setpoints[k] = clip(self.setpoints[k] + 0.000002 * dt * (self.values[k] - self.setpoints[k]), 0.0, 1.0)
            self.errors[k] = round(self.values[k] - self.setpoints[k], 4)
        self.setpoint_ticks += 1
        if self.setpoint_ticks >= 12:
            self.calibrated = True
        self.modulators["da"] = clip(self.modulators["da"] + 0.000001 * dt, 0.0, 1.0)
        self.modulators["serotonin"] = clip(self.modulators["serotonin"] + 0.0000005 * dt, 0.0, 1.0)

    def on_event(self, speaker: str, appraisal) -> None:
        if speaker != "self":
            self.values["social"] = clip(self.values["social"] + 0.02, 0.0, 1.0)
        if appraisal.threat > 0.25:
            self.values["safety"] = clip(self.values["safety"] - 0.05 * appraisal.threat, 0.0, 1.0)
        if appraisal.novelty > 0.55:
            self.values["curiosity"] = clip(self.values["curiosity"] + 0.02, 0.0, 1.0)
            self.modulators["da"] = clip(self.modulators["da"] + 0.01, 0.0, 1.0)
        if speaker != "self":
            self.modulators["octopamine"] = clip(self.modulators["octopamine"] + 0.005, 0.0, 1.0)

    def get(self, name: str) -> float:
        return self.values.get(name, 0.5)

    def allostatic_load(self) -> float:
        return sum(abs(v) for v in self.errors.values()) / max(1, len(self.errors))

    def metabolic_cost(self, activity: float) -> None:
        # neural activity is metabolically expensive
        self.values["energy"] = clip(self.values["energy"] - 0.004 * max(0.0, float(activity)), 0.0, 1.0)

    def load_components(self) -> Dict[str, float]:
        return {
            "physiological": round(abs(self.errors.get("energy", 0.0)), 4),
            "social": round(abs(self.errors.get("social", 0.0)), 4),
            "safety": round(abs(self.errors.get("safety", 0.0)), 4),
        }

    def intrinsic_reward(self) -> float:
        # homeostatic RL: reducing setpoint error is rewarding
        return -self.allostatic_load()

    def dominant_need(self) -> str:
        return max(self.errors, key=lambda k: abs(self.errors[k])) if self.errors else "none"

    def snapshot(self) -> Dict[str, object]:
        return {
            "values": {k: round(v, 4) for k, v in self.values.items()},
            "setpoints": {k: round(v, 4) for k, v in self.setpoints.items()},
            "errors": dict(self.errors),
            "modulators": {k: round(v, 4) for k, v in self.modulators.items()},
            "calibrated": self.calibrated,
            "load_components": self.load_components(),
            "energy": round(self.values["energy"], 4),
            "social": round(self.values["social"], 4),
            "safety": round(self.values["safety"], 4),
            "curiosity": round(self.values["curiosity"], 4),
        }
