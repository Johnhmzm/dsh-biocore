"""Human-like physiology and multi-emotion readout.

This is a modelling layer, not biological realism.  It adds slow bodily
variables and a mutable linear readout into many emotion dimensions.

No behavioural if-rules: physiology is state; emotion is a readout whose
weights are mutable/evolvable.
"""
from __future__ import annotations

import random
from typing import Dict

from .noise import clip, ou


class HumanPhysiology:
    def __init__(self, cfg, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        # body / interoception
        self.hydration = 0.80
        self.temperature = 37.0
        self.pain = 0.0
        self.inflammation = 0.0
        self.sleep_pressure = 0.20
        self.fatigue = 0.10
        self.allostatic = 0.0
        self.nausea = 0.0
        self.itch = 0.0
        # autonomic
        self.heart_rate = 70.0
        self.respiration = 14.0
        self.bp_systolic = 120.0
        self.bp_diastolic = 80.0
        self.oxygen = 0.98
        # endocrine / neuromodulator
        self.cortisol = 0.30
        self.oxytocin = 0.40
        self.adrenaline = 0.10
        self.dopamine = 0.40
        self.serotonin = 0.50
        self.melatonin = 0.10
        self.testosterone = 0.40
        self.estrogen = 0.40
        self.circadian_phase = 0.0
        # low-level evolvable coefficients
        self.params = {
            "hydration_decay": 0.015,
            "sleep_accum": 0.035,
            "sleep_recovery": 0.070,
            "cortisol_gain": 0.60,
            "inflammation_gain": 0.002,
            "fatigue_gain": 0.010,
        }
        # mutable emotion weights
        self.weights: Dict[str, Dict[str, float]] = {
            "fear": {"stress": 0.35, "pain": 0.20, "lonely": 0.15, "uncertainty": 0.15, "cortisol": 0.15},
            "anxiety": {"uncertainty": 0.35, "stress": 0.25, "cortisol": 0.20, "sleep": 0.20},
            "anger": {"stress": 0.30, "pain": 0.20, "lonely": 0.15, "block": 0.25, "adrenaline": 0.10},
            "sadness": {"vneg": 0.40, "fatigue": 0.25, "lonely": 0.20, "inflam": 0.15},
            "joy": {"vpos": 0.45, "oxy": 0.20, "fatigue": -0.20, "pain": -0.10},
            "disgust": {"inflam": 0.35, "nausea": 0.30, "pain": 0.20, "itch": 0.15},
            "surprise": {"novelty": 0.50, "uncertainty": 0.30, "sleep": 0.10},
            "shame": {"social_threat": 0.40, "cortisol": 0.20, "identity_low": 0.20, "uncertainty": 0.20},
            "guilt": {"self_blame": 0.40, "social_threat": 0.20, "cortisol": 0.20},
            "pride": {"agency": 0.40, "goal_congruence": 0.30, "identity": 0.20},
            "love": {"oxy": 0.45, "social": 0.30, "vpos": 0.20},
            "loneliness": {"social_need": 0.55, "vneg": 0.20, "fatigue": 0.15},
            "jealousy": {"comparison": 0.35, "social_need": 0.25, "vneg": 0.20, "cortisol": 0.15},
            "envy": {"comparison": 0.45, "vneg": 0.20, "social_need": 0.20},
            "awe": {"novelty": 0.35, "uncertainty": 0.20, "vpos": 0.25},
            "boredom": {"novelty_low": 0.50, "energy_low": 0.20, "fatigue": 0.20},
            "gratitude": {"social": 0.35, "vpos": 0.30, "oxy": 0.20},
            "contempt": {"social_threat": 0.40, "anger": 0.30, "identity": 0.20},
            "hope": {"vpos": 0.30, "goal_congruence": 0.35, "uncertainty": 0.20},
            "calm": {"stress_low": 0.45, "oxy": 0.25, "sleep": 0.20},
        }

    def set_coefficients(self, params: Dict[str, float]) -> None:
        for k, v in dict(params or {}).items():
            if k in self.params:
                self.params[k] = float(v)

    def set_emotion_weight(self, emotion: str, feature: str, value: float) -> None:
        if emotion in self.weights and feature in self.weights[emotion]:
            self.weights[emotion][feature] = float(value)

    def _ou(self, x: float, target: float, tau_h: float, sigma: float, dh: float) -> float:
        return clip(ou(x, target, 1.0 / max(0.1, tau_h), sigma, dh, self.rng), 0.0, 1.0)

    def update(self, dt: float, energy: float, stress: float, social: float,
               asleep: bool, load: float = 0.0) -> None:
        dh = max(0.0, float(dt)) / 3600.0
        if dh <= 0:
            return
        self.circadian_phase = (self.circadian_phase + dh / 24.0) % 1.0
        self.hydration = clip(self.hydration - self.params["hydration_decay"] * dh + (0.020 if asleep else 0.0) * dh, 0.0, 1.0)
        self.sleep_pressure = clip(self.sleep_pressure + (self.params["sleep_accum"] if not asleep else -self.params["sleep_recovery"]) * dh, 0.0, 1.0)
        self.cortisol = self._ou(self.cortisol, clip(0.20 + self.params["cortisol_gain"] * stress, 0.0, 1.0), 6.0, 0.03, dh)
        self.oxytocin = self._ou(self.oxytocin, clip(0.20 + 0.60 * social, 0.0, 1.0), 12.0, 0.03, dh)
        self.adrenaline = self._ou(self.adrenaline, clip(0.10 + 0.65 * stress + 0.25 * load, 0.0, 1.0), 0.5, 0.04, dh)
        self.dopamine = self._ou(self.dopamine, clip(0.30 + 0.40 * (1.0 - float(energy)) + 0.20 * (1.0 - self.fatigue), 0.0, 1.0), 4.0, 0.03, dh)
        self.serotonin = self._ou(self.serotonin, clip(0.70 - 0.35 * stress, 0.0, 1.0), 10.0, 0.02, dh)
        self.melatonin = self._ou(self.melatonin, clip(0.05 + (0.60 if asleep else 0.0) + 0.25 * (1.0 - self.circadian_phase), 0.0, 1.0), 3.0, 0.02, dh)
        self.inflammation = clip(self.inflammation + self.params["inflammation_gain"] * max(0.0, stress - 0.3) * dh
                                 + 0.001 * load * dh - 0.001 * dh, 0.0, 1.0)
        self.fatigue = clip(self.fatigue + self.params["fatigue_gain"] * max(0.0, stress - 0.2) * dh
                            - (0.030 if asleep else 0.0) * dh, 0.0, 1.0)
        self.pain = clip(self.pain * (1.0 - 0.10 * dh) + 0.01 * max(0.0, load - 0.5) * dh, 0.0, 1.0)
        self.allostatic = clip(self.allostatic + 0.002 * (stress + self.inflammation) * dh
                               - 0.001 * dh, 0.0, 1.0)
        self.nausea = clip(self.nausea + 0.004 * max(0.0, load - 0.4) * dh
                           + 0.002 * self.inflammation * dh - 0.001 * dh, 0.0, 1.0)
        self.itch = clip(self.itch + 0.003 * max(0.0, load - 0.6) * dh - 0.002 * dh, 0.0, 1.0)
        self.temperature = clip(36.5 + 0.6 * self.inflammation + 0.1 * stress
                                + self.rng.gauss(0.0, 0.02), 35.5, 40.0)
        hr_t = 58.0 + 55.0 * stress + 30.0 * self.adrenaline + 12.0 * (1.0 - float(energy)) + 8.0 * self.pain
        self.heart_rate = clip(ou(self.heart_rate, hr_t, 1.0 / 2.0, 2.0, dh, self.rng), 40.0, 180.0)
        resp_t = 11.0 + 10.0 * stress + 8.0 * self.adrenaline + 3.0 * (1.0 - float(energy))
        self.respiration = clip(ou(self.respiration, resp_t, 1.0 / 2.0, 0.5, dh, self.rng), 6.0, 40.0)
        bp_t = 105.0 + 25.0 * stress + 10.0 * self.adrenaline + 5.0 * self.pain
        self.bp_systolic = clip(ou(self.bp_systolic, bp_t, 1.0 / 3.0, 2.0, dh, self.rng), 80.0, 200.0)
        self.bp_diastolic = clip(ou(self.bp_diastolic, bp_t * 0.66, 1.0 / 3.0, 1.5, dh, self.rng), 50.0, 130.0)
        o2_t = 0.985 - 0.05 * self.inflammation - 0.03 * (1.0 - float(energy)) - 0.02 * self.pain
        self.oxygen = clip(ou(self.oxygen, o2_t, 1.0 / 2.0, 0.003, dh, self.rng), 0.80, 1.0)
        # sex hormones are slow, weakly state-coupled; mutable readouts use them
        self.testosterone = self._ou(self.testosterone, clip(0.30 + 0.30 * social - 0.20 * stress, 0.0, 1.0), 24.0, 0.02, dh)
        self.estrogen = self._ou(self.estrogen, clip(0.35 + 0.20 * (1.0 - stress) + 0.15 * (1.0 - self.fatigue), 0.0, 1.0), 24.0, 0.02, dh)

    def interoception(self, energy: float = 0.5, social: float = 0.5) -> Dict[str, float]:
        return {
            "hunger": round(clip(1.0 - float(energy), 0.0, 1.0), 4),
            "thirst": round(clip(1.0 - self.hydration, 0.0, 1.0), 4),
            "pain": round(self.pain, 4),
            "inflammation": round(self.inflammation, 4),
            "sleep_pressure": round(self.sleep_pressure, 4),
            "fatigue": round(self.fatigue, 4),
            "cortisol": round(self.cortisol, 4),
            "oxytocin": round(self.oxytocin, 4),
            "allostatic": round(self.allostatic, 4),
            "temperature": round(self.temperature, 3),
            "loneliness": round(clip(1.0 - float(social), 0.0, 1.0), 4),
            "heart_rate": round(self.heart_rate, 2),
            "respiration": round(self.respiration, 2),
            "bp_systolic": round(self.bp_systolic, 2),
            "bp_diastolic": round(self.bp_diastolic, 2),
            "oxygen": round(self.oxygen, 4),
            "adrenaline": round(self.adrenaline, 4),
            "dopamine": round(self.dopamine, 4),
            "serotonin": round(self.serotonin, 4),
            "melatonin": round(self.melatonin, 4),
            "nausea": round(self.nausea, 4),
            "itch": round(self.itch, 4),
            "testosterone": round(self.testosterone, 4),
            "estrogen": round(self.estrogen, 4),
        }

    def emotion_profile(self, v: float = 0.0, stress: float = 0.0, social: float = 0.5,
                        novelty: float = 0.0, uncertainty: float = 0.5,
                        energy: float = 0.5, agency: float = 0.0,
                        goal_congruence: float = 0.0, identity: float = 0.5,
                        comparison: float = 0.0) -> Dict[str, float]:
        v = float(v); stress = float(stress); social = float(social)
        x = {
            "vpos": max(0.0, v), "vneg": max(0.0, -v),
            "stress": stress, "stress_low": 1.0 - stress,
            "pain": self.pain, "inflam": self.inflammation, "lonely": clip(1.0 - social, 0.0, 1.0),
            "social": social, "social_need": clip(1.0 - social, 0.0, 1.0), "social_threat": clip(1.0 - social, 0.0, 1.0),
            "uncertainty": float(uncertainty), "novelty": float(novelty), "novelty_low": 1.0 - float(novelty),
            "sleep": self.sleep_pressure, "fatigue": self.fatigue, "energy_low": clip(1.0 - float(energy), 0.0, 1.0),
            "oxy": self.oxytocin, "cortisol": self.cortisol, "adrenaline": self.adrenaline,
            "agency": max(0.0, float(agency)), "self_blame": max(0.0, -float(agency)),
            "goal_congruence": float(goal_congruence), "block": max(0.0, -float(goal_congruence)),
            "identity": float(identity), "identity_low": clip(1.0 - float(identity), 0.0, 1.0),
            "comparison": float(comparison), "nausea": self.nausea, "itch": self.itch,
        }
        out = {}
        for name, ws in self.weights.items():
            z = sum(float(w) * float(x.get(k, 0.0)) for k, w in ws.items())
            out[name] = round(clip(z, 0.0, 1.0), 4)
        return out

    def snapshot(self) -> Dict[str, float]:
        return {
            "hydration": round(self.hydration, 4), "temperature": round(self.temperature, 3),
            "pain": round(self.pain, 4), "inflammation": round(self.inflammation, 4),
            "sleep_pressure": round(self.sleep_pressure, 4), "cortisol": round(self.cortisol, 4),
            "oxytocin": round(self.oxytocin, 4), "fatigue": round(self.fatigue, 4),
            "allostatic": round(self.allostatic, 4), "nausea": round(self.nausea, 4), "itch": round(self.itch, 4),
            "heart_rate": round(self.heart_rate, 2), "respiration": round(self.respiration, 2),
            "bp_systolic": round(self.bp_systolic, 2), "bp_diastolic": round(self.bp_diastolic, 2),
            "oxygen": round(self.oxygen, 4), "adrenaline": round(self.adrenaline, 4),
            "dopamine": round(self.dopamine, 4), "serotonin": round(self.serotonin, 4),
            "melatonin": round(self.melatonin, 4), "testosterone": round(self.testosterone, 4),
            "estrogen": round(self.estrogen, 4), "circadian_phase": round(self.circadian_phase, 4),
        }
