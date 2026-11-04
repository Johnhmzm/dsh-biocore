from __future__ import annotations

import random
from typing import Dict

from .config import MindConfig
from .noise import clip, ou
from .types import Appraisal


class Affect:
    """Fast affect (V/A/stress) + slow mood + neuromodulator scalars."""

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.v = 0.0
        self.a = 0.5
        self.dominance = 0.0
        self.stress = 0.0
        self.scar = 0.0
        self.mood = 0.0
        self.fatigue = 0.0
        self.da = 0.5
        self.oa = 0.5
        self.ht = 0.5

    def update(self, ap: Appraisal, drives, dt: float) -> None:
        v_target = clip(0.60 * ap.valence + 0.20 * self.mood + 0.20 * ap.social, -1.0, 1.0)
        a_target = clip(
            0.40 + 0.30 * ap.novelty + 0.20 * ap.threat + 0.10 * (1.0 - drives.get("energy")),
            0.0,
            1.0,
        )
        stress_target = clip(
            0.50 * ap.threat + 0.30 * max(0.0, -ap.valence) + 0.20 * (1.0 - drives.get("safety")),
            0.0,
            1.0,
        )
        theta_fast = 1.0 / max(self.cfg.affect_tau, 1e-6)
        theta_stress = 1.0 / max(self.cfg.stress_tau, 1e-6)
        theta_mood = 1.0 / max(self.cfg.mood_tau, 1e-6)
        self.v = ou(self.v, v_target, theta_fast, self.cfg.noise_sigma, dt, self.rng)
        self.a = ou(self.a, a_target, theta_fast, self.cfg.noise_sigma, dt, self.rng)
        self.stress = ou(self.stress, stress_target, theta_stress, self.cfg.noise_sigma, dt, self.rng)
        dominance_target = clip(0.5 * ap.agency + 0.25 * ap.control + 0.25 * (1.0 - ap.threat) - 0.2 * self.stress, -1.0, 1.0)
        self.dominance = ou(self.dominance, dominance_target, theta_fast, self.cfg.noise_sigma * 0.5, dt, self.rng)
        self.dominance = clip(self.dominance, -1.0, 1.0)
        mood_target = clip(0.70 * self.v + 0.30 * self.mood, -1.0, 1.0)
        self.mood = ou(self.mood, mood_target, theta_mood, self.cfg.noise_sigma * 0.5, dt, self.rng)
        self.v = clip(self.v, -1.0, 1.0)
        self.a = clip(self.a, 0.0, 1.0)
        self.stress = clip(self.stress, 0.0, 1.0)
        # acute load can recover; a slow scar accumulates under chronic stress
        grow = 0.00002 * dt * max(0.0, self.stress - self.scar)
        decay = 0.0000002 * dt * self.scar
        self.scar = clip(self.scar + grow - decay, 0.0, 1.0)
        self.mood = clip(self.mood, -1.0, 1.0)
        # neuromodulators: DA tracks RPE, OA tracks arousal, 5-HT tracks patience
        self.da = clip(self.da + 0.08 * (ap.rpe - 0.10 * self.da), 0.0, 1.0)
        self.oa = clip(self.oa + 0.08 * (self.a - self.oa), 0.0, 1.0)
        self.ht = clip(self.ht + 0.03 * ((1.0 - self.stress) - self.ht), 0.0, 1.0)

    def update_structural_load(self, load: float, dt: float) -> None:
        # crowded/overloaded circuitry adds metabolic fatigue
        self.fatigue = clip(self.fatigue + 0.002 * max(0.0, float(load)) * (max(0.0, float(dt)) / 3600.0), 0.0, 1.0)

    def update_self_efficacy(self, ase: float, dt: float) -> None:
        # low allostatic self-efficacy accumulates fatigue
        target = clip(1.0 - float(ase), 0.0, 1.0)
        rate = min(1.0, max(0.0, dt) / (6.0 * 3600.0))
        self.fatigue = clip(self.fatigue + (target - self.fatigue) * rate, 0.0, 1.0)

    def snapshot(self) -> Dict[str, float]:
        return {
            "v": round(self.v, 4),
            "a": round(self.a, 4),
            "dominance": round(self.dominance, 4),
            "stress": round(self.stress, 4),
            "scar": round(self.scar, 4),
            "mood": round(self.mood, 4),
            "fatigue": round(self.fatigue, 3),
            "da": round(self.da, 4),
            "oa": round(self.oa, 4),
            "ht": round(self.ht, 4),
        }
