"""Biological parameter adaptation, not prompt/persona iteration.

The LLM can propose small trait deltas from interaction evidence. The bio layer
clamps them, drifts them, persists them, and feeds the updated numbers back into
the next prompt. This is the closed loop: language -> bio -> language.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .noise import clip

TRAITS = (
    "O", "C", "E", "A", "N",
    "stress_reactivity", "recovery_rate", "social_baseline",
    "energy_baseline", "curiosity_baseline", "safety_baseline",
    "da_baseline", "oa_baseline", "ht_baseline", "autonomy_gain",
)


@dataclass
class BioProfile:
    traits: dict = field(default_factory=dict)
    version: int = 1
    evidence: int = 0
    last_update: float = 0.0
    baseline: dict = field(default_factory=dict)
    stress_load: float = 0.0
    last_tick: float = 0.0
    noise_values: dict = field(default_factory=dict)
    noise_chaos: float = 0.5
    last_noise_tick: float = 0.0
    noise_echo: float = 0.0
    echo_trace: float = 0.0
    last_echo_tick: float = 0.0
    persona_version: str = ""

    def __post_init__(self):
        if not self.traits:
            self.traits = {k: 0.5 for k in TRAITS}
        if not self.baseline:
            self.baseline = dict(self.traits)

    @classmethod
    def from_mind(cls, mind) -> "BioProfile":
        p = getattr(mind, "persona", None)
        traits = {}
        for k in TRAITS:
            try:
                traits[k] = float(getattr(p, k))
            except Exception:
                traits[k] = 0.5
        return cls(traits=traits, baseline=dict(traits))

    def nudge(self, deltas: dict, confidence: float = 0.5) -> dict:
        c = clip(confidence, 0.0, 1.0)
        lr = 0.12 * c
        for k, dv in dict(deltas or {}).items():
            if k not in TRAITS:
                continue
            try:
                dv = clip(float(dv), -0.02, 0.02)
            except Exception:
                continue
            self.traits[k] = clip(float(self.traits[k]) + lr * dv, 0.0, 1.0)
        self.version += 1
        self.evidence += 1
        self.last_update = time.time()
        return dict(self.traits)

    def experience(self, reward: float = 0.5, valence: float = 0.0,
                   stress: float = 0.2, social: float = 0.5) -> dict:
        """Slow learning from actual outcomes; LLM proposal is only one input."""
        r = clip(reward, 0.0, 1.0)
        v = clip(valence, -1.0, 1.0)
        st = clip(stress, 0.0, 1.0)
        so = clip(social, 0.0, 1.0)
        lr = 0.004
        # positive + low stress -> recovery/safety/social up
        if r >= 0.55 and st < 0.65:
            self.traits["recovery_rate"] = clip(self.traits["recovery_rate"] + lr * (r - 0.5), 0.0, 1.0)
            self.traits["safety_baseline"] = clip(self.traits["safety_baseline"] + lr * (r - 0.5) * 0.8, 0.0, 1.0)
            self.traits["social_baseline"] = clip(self.traits["social_baseline"] + lr * (r - 0.5) * 0.6 * (0.4 + so), 0.0, 1.0)
        elif r < 0.45 and st > 0.6:
            self.traits["stress_reactivity"] = clip(self.traits["stress_reactivity"] + lr * (0.5 - r) * 0.7, 0.0, 1.0)
            self.traits["recovery_rate"] = clip(self.traits["recovery_rate"] - lr * (0.5 - r) * 0.4, 0.0, 1.0)
        if v > 0.2:
            self.traits["curiosity_baseline"] = clip(self.traits["curiosity_baseline"] + lr * 0.4 * v, 0.0, 1.0)
        self.version += 1
        self.last_update = time.time()
        return dict(self.traits)

    def drift(self, rate: float = 0.002) -> None:
        for k in TRAITS:
            self.traits[k] = clip(float(self.traits[k]) + rate * (float(self.baseline[k]) - float(self.traits[k])), 0.0, 1.0)

    def apply_to_mind(self, mind) -> None:
        p = getattr(mind, "persona", None)
        if p is None:
            return
        for k, v in self.traits.items():
            if hasattr(p, k):
                try:
                    setattr(p, k, clip(float(v), 0.0, 1.0))
                except Exception:
                    pass
        # do not reset current affect/drives; only move their baselines slowly
        try:
            mind.drives.values["social"] = clip(0.995 * float(mind.drives.values.get("social", 0.5)) + 0.005 * float(self.traits["social_baseline"]), 0.0, 1.0)
            mind.drives.values["curiosity"] = clip(0.995 * float(mind.drives.values.get("curiosity", 0.5)) + 0.005 * float(self.traits["curiosity_baseline"]), 0.0, 1.0)
            mind.drives.values["energy"] = clip(0.995 * float(mind.drives.values.get("energy", 0.5)) + 0.005 * float(self.traits["energy_baseline"]), 0.0, 1.0)
        except Exception:
            pass

    def _noise_engine(self):
        eng = getattr(self, "_noise_eng", None)
        if eng is None:
            from .bio_noise import BioNoise
            eng = BioNoise(2026)
            eng.values.update({k: float(v) for k, v in (self.noise_values or {}).items()})
            eng.last_chaos = float(self.noise_chaos or 0.5)
            self._noise_eng = eng
        return eng

    def update_echo(self, instant: float, now: float | None = None,
                    tau_s: float = 180.0) -> float:
        """Echo propagation with attenuation: e_t = a*e_(t-1) + (1-a)*instant."""
        now = time.time() if now is None else float(now)
        if float(self.last_echo_tick or 0.0) <= 0.0:
            dt = 5.0
        else:
            dt = max(0.0, min(600.0, now - float(self.last_echo_tick)))
        a = math.exp(-dt / max(1.0, float(tau_s)))
        self.echo_trace = clip(a * float(self.echo_trace or 0.0) + (1.0 - a) * float(instant), -1.0, 1.0)
        self.noise_echo = self.echo_trace
        self.last_echo_tick = now
        return self.echo_trace

    def step_noise(self, now: float | None = None) -> dict:
        now = time.time() if now is None else float(now)
        eng = self._noise_engine()
        if float(self.last_noise_tick or 0.0) <= 0.0:
            dt = 1.0
        else:
            dt = max(0.0, min(30.0, now - float(self.last_noise_tick)))
        vals = eng.step(dt)
        self.noise_values = {k: round(float(v), 4) for k, v in vals.items()}
        self.noise_chaos = round(float(eng.last_chaos), 4)
        self.last_noise_tick = now
        return dict(self.noise_values)

    def tick(self, mind, now: float | None = None, noise: bool = True) -> dict:
        """Stress hysteresis: rise fast, recover slowly. This is the missing inertia."""
        now = time.time() if now is None else float(now)
        try:
            stress = clip(float(getattr(mind.affect, "stress", 0.2)), 0.0, 1.0)
        except Exception:
            stress = 0.2
        if float(self.last_tick or 0.0) <= 0.0:
            dt = 0.0
        else:
            dt = max(0.0, now - float(self.last_tick))
        # recovery time: 2h base, slower when recovery_rate is low
        tau_s = 2.0 * 3600.0 + 4.0 * 3600.0 * (1.0 - clip(float(self.traits.get("recovery_rate", 0.5)), 0.0, 1.0))
        if dt > 0.0:
            self.stress_load = clip(float(self.stress_load) * math.exp(-dt / tau_s), 0.0, 1.0)
        # high stress loads immediately; low stress decays slowly
        self.stress_load = max(float(self.stress_load), stress)
        self.last_tick = now
        if noise:
            self.step_noise(now)
        return {"stress": round(stress, 4), "stress_load": round(float(self.stress_load), 4),
                "tau_hours": round(tau_s / 3600.0, 2)}

    def behavior(self, mind, base_threshold: float = 0.74, trust: float = 0.5) -> dict:
        """Bio state -> hard behavioral consequences, not just prompt text."""
        energy = 0.5
        stress = 0.2
        social = 0.5
        trust = clip(float(trust), 0.0, 1.0)
        try:
            energy = clip(float(mind.drives.values.get("energy", 0.5)), 0.0, 1.0)
            social = clip(float(mind.drives.values.get("social", 0.5)), 0.0, 1.0)
            stress = clip(float(getattr(mind.affect, "stress", 0.2)), 0.0, 1.0)
        except Exception:
            pass
        recovery = clip(float(self.traits.get("recovery_rate", 0.5)), 0.0, 1.0)
        n = dict(self.noise_values or {})
        echo = clip(float(self.noise_echo or 0.0), -1.0, 1.0)
        stress_eff = clip(max(stress, float(self.stress_load or 0.0)) + 0.12 * float(n.get("stress", 0.0)), 0.0, 1.0)
        energy_eff = clip(energy + 0.10 * float(n.get("energy", 0.0)), 0.0, 1.0)
        # low energy/high stress/low recovery -> harder to join a group
        threshold = (float(base_threshold) + 0.12 * (1.0 - energy_eff)
                     + 0.08 * stress_eff - 0.05 * (trust - 0.5)
                     + 0.04 * float(n.get("attention", 0.0)) - 0.03 * float(n.get("social", 0.0))
                     + 0.03 * echo
                     - 0.04 * (social - 0.5) + 0.06 * (0.5 - recovery))
        threshold = clip(threshold, 0.68, 0.92)
        # short replies when depleted/stressed; forced replies keep a floor
        max_chars = int(240.0 * (0.55 + 0.45 * energy_eff) * (1.0 - 0.30 * stress_eff))
        max_chars = int(max(80, min(260, max_chars)))
        private_mode = "answer"
        if energy_eff < 0.22 and stress_eff > 0.75:
            private_mode = "defer"
        elif energy_eff < 0.40 or stress_eff > 0.70:
            private_mode = "brief"
        warmth = clip(0.50 + 0.20 * trust + 0.20 * float(getattr(mind.affect, "v", 0.0))
                      - 0.25 * stress_eff, 0.0, 1.0)
        disclosure = clip(0.20 + 0.55 * trust + 0.15 * energy_eff - 0.25 * stress_eff, 0.0, 1.0)
        initiative = clip(0.20 + 0.35 * social + 0.25 * energy_eff - 0.30 * stress_eff, 0.0, 1.0)
        return {"threshold": round(threshold, 4), "max_chars": max_chars,
                "energy": round(energy, 3), "stress": round(stress, 3),
                "stress_load": round(stress_eff, 3),
                "private_mode": private_mode,
                "warmth": round(warmth, 3),
                "disclosure": round(disclosure, 3),
                "initiative": round(initiative, 3),
                "noise": {k: round(float(v), 3) for k, v in n.items()},
                "chaos": round(float(self.noise_chaos or 0.5), 3),
                "echo": round(echo, 3), "echo_trace": round(float(self.echo_trace or 0.0), 3),
                "echo_tau_s": 180.0,
                "social": round(social, 3), "trust": round(trust, 3),
                "recovery": round(recovery, 3)}

    def context(self) -> str:
        keys = ("stress_reactivity", "recovery_rate", "social_baseline",
                "curiosity_baseline", "safety_baseline", "energy_baseline")
        return " ".join("%s=%.2f" % (k, float(self.traits.get(k, 0.5))) for k in keys)

    def snapshot(self) -> dict:
        return {"version": self.version, "evidence": self.evidence,
                "last_update": self.last_update,
                "stress_load": round(float(self.stress_load or 0.0), 4),
                "noise_values": {k: round(float(v), 4) for k, v in (self.noise_values or {}).items()},
                "noise_chaos": round(float(self.noise_chaos or 0.5), 4),
                "last_noise_tick": self.last_noise_tick,
                "noise_echo": round(float(self.noise_echo or 0.0), 4),
                "echo_trace": round(float(self.echo_trace or 0.0), 4),
                "last_echo_tick": self.last_echo_tick,
                "traits": {k: round(float(v), 4) for k, v in self.traits.items()}}

    def save(self, path: Path | str) -> None:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def load(cls, path: Path | str, mind=None) -> "BioProfile":
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            obj = cls(traits=dict(d.get("traits") or {}), version=int(d.get("version", 1)),
                      evidence=int(d.get("evidence", 0)), last_update=float(d.get("last_update", 0.0)),
                      baseline=dict(d.get("baseline") or {}),
                      stress_load=float(d.get("stress_load", 0.0) or 0.0),
                      last_tick=float(d.get("last_tick", 0.0) or 0.0),
                      noise_values=dict(d.get("noise_values") or {}),
                      noise_chaos=float(d.get("noise_chaos", 0.5) or 0.5),
                      last_noise_tick=float(d.get("last_noise_tick", 0.0) or 0.0),
                      noise_echo=float(d.get("noise_echo", 0.0) or 0.0),
                      echo_trace=float(d.get("echo_trace", 0.0) or 0.0),
                      last_echo_tick=float(d.get("last_echo_tick", 0.0) or 0.0),
                      persona_version=str(d.get("persona_version") or ""))
            if not obj.traits and mind is not None:
                return cls.from_mind(mind)
            return obj
        except Exception:
            return cls.from_mind(mind) if mind is not None else cls()
