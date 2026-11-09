from __future__ import annotations

import math
import random
from typing import Dict

from .config import MindConfig
from .noise import clip


class Autonomy:
    """Endogenous accumulator: no external trigger required to act."""

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.h = 0.0
        self.last_spoke = -1e9
        self.gain = 1.0
        # learned meta-gain, not a hand-tuned rule
        self.allostatic_gain = 0.05
        self.gain_lr = 0.01
        # learned impulse threshold, initialized from config
        self.threshold = cfg.autonomy_threshold
        self.threshold_lr = 0.002
        self.tau = cfg.autonomy_tau
        self.cooldown = cfg.autonomy_cooldown
        self.tau_lr = 0.005
        self.activity_ema = 0.0
        self.adapt_lr = float(getattr(cfg, "autonomy_adapt_lr", 0.004))
        self.viability_target = float(getattr(cfg, "autonomy_viability_target", 0.90))
        self.social_target = float(getattr(cfg, "autonomy_social_target", 0.50))
        self.activity_target = float(getattr(cfg, "autonomy_activity_target", 0.30))

    def tick(self, dt: float, clock, drives, mood: float, allostatic: float = 0.0, fatigue: float = 0.0, attachment_bias: float = 1.0) -> None:
        if dt <= 0:
            return
        brake = max(0.2, 1.0 - 0.5 * float(fatigue))
        drive = brake * float(attachment_bias) * self.gain * (clock.drive + 0.20 * drives.get("social") + 0.20 * max(0.0, mood) + self.allostatic_gain * float(allostatic))
        tau = max(self.tau, 1e-6)
        noise = self.rng.gauss(0.0, 0.01) * math.sqrt(dt / tau)
        self.h = self.h * math.exp(-dt / tau) + drive * (dt / tau) + noise

    def due(self, now: float, asleep: bool) -> bool:
        if asleep:
            return False
        return self.h > self.threshold and (now - self.last_spoke) > self.cooldown

    def adapt(self, now: float, viability: float, social_need: float, stress: float,
              spoke: bool, dt: float) -> float:
        """Intrinsic homeostatic adaptation of the impulse threshold. No external labels."""
        if dt <= 0:
            return self.threshold
        spoke_f = 1.0 if spoke else 0.0
        self.activity_ema += 0.02 * (spoke_f - self.activity_ema)
        # low viability -> raise threshold (be conservative); high social need -> lower
        err = (self.viability_target - float(viability)) - 0.5 * (float(social_need) - self.social_target)
        err += 0.15 * (self.activity_target - self.activity_ema)
        rate = self.adapt_lr * min(1.0, max(0.1, float(dt) / 60.0))
        self.threshold = clip(self.threshold + rate * err, 0.20, 2.0)
        return self.threshold

    def on_speak(self, now: float) -> None:
        self.h *= 0.30
        self.last_spoke = now

    def learn_from_feedback(self, rpe: float, allostatic: float) -> None:
        # RPE meta-gradient: raise/lower allostatic sensitivity from outcome
        self.allostatic_gain = clip(
            self.allostatic_gain + self.gain_lr * float(rpe) * float(allostatic), 0.0, 0.5
        )
        # outcome learning of the impulse threshold
        self.threshold = clip(
            self.threshold - self.threshold_lr * float(rpe) * min(1.0, abs(self.h)), 0.35, 2.0
        )
        # positive outcome -> faster accumulation / shorter cooldown
        self.tau = clip(self.tau * (1.0 - self.tau_lr * float(rpe)), 60.0, 7200.0)
        self.cooldown = clip(self.cooldown - 20.0 * float(rpe), 10.0, 3600.0)

    def snapshot(self) -> Dict[str, float]:
        return {"h": round(self.h, 4), "last_spoke": round(self.last_spoke, 1), "allostatic_gain": round(self.allostatic_gain, 4), "threshold": round(self.threshold, 4), "tau": round(self.tau, 1), "cooldown": round(self.cooldown, 1), "viability_target": round(self.viability_target, 4), "social_target": round(self.social_target, 4), "activity_target": round(self.activity_target, 4), "activity_ema": round(self.activity_ema, 4)}
