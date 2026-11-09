from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict

from .constructs import ConstructRegistry
from .noise import clip, ou


@dataclass
class Persona:
    """Personality as a parameter seed, not a prompt. Same code + new seed = new person."""

    seed: int
    O: float
    C: float
    E: float
    A: float
    N: float
    baseline_v: float
    baseline_a: float
    noise_scale: float
    stress_reactivity: float = 0.5
    recovery_rate: float = 0.7
    social_baseline: float = 0.6
    energy_baseline: float = 0.7
    curiosity_baseline: float = 0.6
    safety_baseline: float = 0.7
    da_baseline: float = 0.5
    oa_baseline: float = 0.5
    ht_baseline: float = 0.5
    autonomy_gain: float = 1.0
    learning_rate: float = 0.05
    circadian_phase: float = 0.0
    constructs: Dict[str, float] = field(default_factory=dict)
    extra: Dict[str, float] = field(default_factory=dict)

    def refresh_numbers(self) -> None:
        """Big Five -> physiology / biology numbers injected into lower layers."""
        self.stress_reactivity = clip(0.25 + 0.55 * self.N, 0.0, 1.0)
        self.recovery_rate = clip(1.00 - 0.55 * self.N, 0.0, 1.0)
        self.social_baseline = clip(0.30 + 0.55 * self.E, 0.0, 1.0)
        self.energy_baseline = clip(0.50 + 0.30 * self.C, 0.0, 1.0)
        self.curiosity_baseline = clip(0.30 + 0.55 * self.O, 0.0, 1.0)
        self.safety_baseline = clip(0.50 + 0.25 * self.C + 0.20 * self.N, 0.0, 1.0)
        self.da_baseline = clip(0.30 + 0.40 * self.E + 0.15 * self.O, 0.0, 1.0)
        self.oa_baseline = clip(0.35 + 0.35 * self.N + 0.20 * self.E, 0.0, 1.0)
        self.ht_baseline = clip(0.40 + 0.30 * self.A + 0.20 * self.C, 0.0, 1.0)
        self.autonomy_gain = clip(0.75 + 0.50 * self.E - 0.20 * self.N, 0.0, 1.5)
        self.learning_rate = clip(0.03 + 0.04 * self.C + 0.02 * self.O, 0.01, 0.12)
        self.circadian_phase = clip(0.20 * (self.O - 0.50), -0.10, 0.10)

    def numbers(self) -> Dict[str, float]:
        return {
            "O": self.O,
            "C": self.C,
            "E": self.E,
            "A": self.A,
            "N": self.N,
            "baseline_v": self.baseline_v,
            "baseline_a": self.baseline_a,
            "stress_reactivity": self.stress_reactivity,
            "recovery_rate": self.recovery_rate,
            "social_baseline": self.social_baseline,
            "energy_baseline": self.energy_baseline,
            "curiosity_baseline": self.curiosity_baseline,
            "safety_baseline": self.safety_baseline,
            "da_baseline": self.da_baseline,
            "oa_baseline": self.oa_baseline,
            "ht_baseline": self.ht_baseline,
            "autonomy_gain": self.autonomy_gain,
            "learning_rate": self.learning_rate,
            "circadian_phase": self.circadian_phase,
        }

    def apply_to(self, mb) -> None:
        """Inject the persona numbers into L1/L2/L3/L4 modules."""
        mb.drives.values["energy"] = self.energy_baseline
        mb.drives.values["social"] = self.social_baseline
        mb.drives.values["safety"] = self.safety_baseline
        mb.drives.values["curiosity"] = self.curiosity_baseline
        mb.affect.v = self.baseline_v
        mb.affect.a = self.baseline_a
        mb.affect.da = self.da_baseline
        mb.affect.oa = self.oa_baseline
        mb.affect.ht = self.ht_baseline
        mb.affect.stress = clip(0.10 * self.stress_reactivity, 0.0, 1.0)
        mb.autonomy.gain = self.autonomy_gain
        mb.will.willpower = clip(0.45 + 0.55 * self.C - 0.25 * self.N, 0.10, 1.0)
        mb.mb.lr = self.learning_rate
        try:
            mb.social.trust_gain = clip(0.60 + 0.90 * self.A - 0.30 * self.N, 0.3, 1.8)
            mb.self_model.identity_gain = clip(0.70 + 0.60 * self.O + 0.30 * self.E - 0.20 * self.N, 0.4, 1.6)
            mb.self_model.identity_setpoint_base = clip(0.65 + 0.10 * self.E + 0.05 * self.O - 0.05 * self.N, 0.55, 0.80)
            mb.self_model.identity = mb.self_model.identity_setpoint_base
        except Exception:
            pass
        mb.clock.phase = self.circadian_phase

    def apply_constructs(self, scores: Dict[str, float], registry: ConstructRegistry | None = None) -> Dict[str, float]:
        """Apply declarative construct scores to the numeric profile."""
        reg = registry or ConstructRegistry()
        updated, extra = reg.apply(self.numbers(), scores)
        for key, value in updated.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.extra.update({k: round(v, 4) for k, v in extra.items()})
        self.constructs.update({k: round(clip(float(v), 0.0, 1.0), 3) for k, v in scores.items() if reg.has(k)})
        return self.extra

    @classmethod
    def from_profile(cls, profile: Dict[str, float], seed: int = 0) -> "Persona":
        p = cls.from_seed(seed)
        aliases = {
            "o": "O",
            "openness": "O",
            "c": "C",
            "conscientiousness": "C",
            "e": "E",
            "extraversion": "E",
            "a": "A",
            "agreeableness": "A",
            "n": "N",
            "neuroticism": "N",
        }
        numeric = set(p.numbers())
        for key, value in profile.items():
            k = aliases.get(str(key).lower(), key)
            if k in ("O", "C", "E", "A", "N"):
                setattr(p, k, clip(float(value), 0.0, 1.0))
        p.refresh_numbers()
        for key, value in profile.items():
            if key in numeric and key not in ("O", "C", "E", "A", "N"):
                setattr(p, key, float(value))
        registry = ConstructRegistry()
        construct_scores: Dict[str, float] = {}
        nested = profile.get("constructs")
        if isinstance(nested, dict):
            construct_scores.update({k: float(v) for k, v in nested.items()})
        for key, value in profile.items():
            if registry.has(key):
                construct_scores[key] = float(value)
        p.apply_constructs(construct_scores, registry)
        extra = profile.get("extra")
        if isinstance(extra, dict):
            p.extra.update({k: clip(float(v), 0.0, 1.0) for k, v in extra.items()})
        return p

    @classmethod
    def from_prompt(cls, prompt: str, seed: int = 0) -> "Persona":
        """Heuristic fallback: persona keywords -> Big Five. LLM parser can replace this."""
        p = cls.from_seed(seed)
        text = prompt.lower()
        bumps = {
            "E": (("外向", "开朗", "活泼", "健谈", "extravert", "outgoing"), 0.20, 0.72),
            "O": (("好奇", "探索", "开放", "想象", "curious", "open"), 0.20, 0.72),
            "A": (("温和", "体贴", "共情", "友善", "kind", "agreeable"), 0.20, 0.70),
            "C": (("自律", "认真", "有条理", "负责", "conscientious", "organized"), 0.20, 0.72),
            "N": (("敏感", "焦虑", "紧张", "易受伤", "anxious", "sensitive"), 0.20, 0.62),
        }
        for trait, (words, delta, floor) in bumps.items():
            if any(w in text for w in words):
                current = getattr(p, trait)
                setattr(p, trait, clip(max(current + delta, floor), 0.0, 1.0))
        p.refresh_numbers()
        construct_scores: Dict[str, float] = {}
        if any(w in text for w in ("微笑抑郁", "掩饰抑郁", "smiling", "masked")):
            construct_scores["camouflaging_depression"] = 0.70
        if any(w in text for w in ("表达抑制", "压抑", "suppress")):
            construct_scores["expressive_suppression"] = 0.80
        if any(w in text for w in ("自我沉默", "不敢说", "silenc")):
            construct_scores["self_silencing"] = 0.80
        p.apply_constructs(construct_scores)
        return p

    @classmethod
    def from_seed(cls, seed: int) -> "Persona":
        rng = random.Random(seed)
        p = cls(
            seed=seed,
            O=clip(0.5 + rng.gauss(0.0, 0.18), 0.0, 1.0),
            C=clip(0.5 + rng.gauss(0.0, 0.18), 0.0, 1.0),
            E=clip(0.5 + rng.gauss(0.0, 0.18), 0.0, 1.0),
            A=clip(0.5 + rng.gauss(0.0, 0.18), 0.0, 1.0),
            N=clip(0.5 + rng.gauss(0.0, 0.18), 0.0, 1.0),
            baseline_v=clip(rng.gauss(0.05, 0.15), -1.0, 1.0),
            baseline_a=clip(0.5 + rng.gauss(0.0, 0.10), 0.0, 1.0),
            noise_scale=0.8 + 0.4 * rng.random(),
        )
        p.refresh_numbers()
        return p

    def drift(self, dt: float, rng: random.Random) -> None:
        # very slow drift; keeps identity but allows aging/learning
        theta = 1.0 / (7.0 * 86400.0)
        self.O = clip(ou(self.O, 0.5, theta, 0.01, dt, rng), 0.0, 1.0)
        self.C = clip(ou(self.C, 0.5, theta, 0.01, dt, rng), 0.0, 1.0)
        self.E = clip(ou(self.E, 0.5, theta, 0.01, dt, rng), 0.0, 1.0)
        self.A = clip(ou(self.A, 0.5, theta, 0.01, dt, rng), 0.0, 1.0)
        self.N = clip(ou(self.N, 0.5, theta, 0.01, dt, rng), 0.0, 1.0)
        self.refresh_numbers()

    def snapshot(self) -> Dict[str, float]:
        return {
            "seed": self.seed,
            "O": round(self.O, 3),
            "C": round(self.C, 3),
            "E": round(self.E, 3),
            "A": round(self.A, 3),
            "N": round(self.N, 3),
            "baseline_v": round(self.baseline_v, 3),
            "baseline_a": round(self.baseline_a, 3),
            "stress_reactivity": round(self.stress_reactivity, 3),
            "social_baseline": round(self.social_baseline, 3),
            "curiosity_baseline": round(self.curiosity_baseline, 3),
            "autonomy_gain": round(self.autonomy_gain, 3),
            "learning_rate": round(self.learning_rate, 3),
            "constructs": dict(self.constructs),
            "extra": dict(self.extra),
        }
