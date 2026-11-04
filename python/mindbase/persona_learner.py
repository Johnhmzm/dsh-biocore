"""Persona numeric evolution from chat feedback.

The full persona prompt stays in the LLM layer.  This module only evolves the
numeric tendency vector with a slow evolutionary-strategy update.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path
from typing import Dict, List, Tuple

from .noise import clip

PATHS: List[Tuple[str, str]] = [
    ("affect", "baseline_v"), ("affect", "baseline_a"), ("affect", "stress_reactivity"),
    ("affect", "recovery_rate"), ("affect", "volatility"),
    ("drives", "social"), ("drives", "energy"), ("drives", "curiosity"),
    ("drives", "safety"), ("drives", "achievement"), ("drives", "autonomy_need"),
    ("temperament", "warmth"), ("temperament", "playfulness"), ("temperament", "directness"),
    ("temperament", "verbosity"), ("temperament", "formality"), ("temperament", "teasing"),
    ("temperament", "initiative"), ("temperament", "boundary"),
    ("temperament", "emotional_expressiveness"), ("temperament", "stability"),
    ("social", "trust_gain"), ("social", "attachment_anxiety"), ("social", "attachment_avoidance"),
    ("social", "empathy"), ("social", "conflict_approach"), ("social", "self_disclosure"),
    ("learning", "empathy_gain_init"), ("learning", "learning_rate"), ("learning", "autonomy_gain"),
]


def _get(d: Dict[str, object], group: str, key: str) -> float:
    return float((d.get(group) or {}).get(key, 0.5))


def _put(d: Dict[str, object], group: str, key: str, value: float) -> None:
    d.setdefault(group, {})
    d[group][key] = value


def _bounds(group: str, key: str) -> Tuple[float, float]:
    if (group, key) == ("affect", "baseline_v"):
        return (-1.0, 1.0)
    if (group, key) == ("social", "trust_gain"):
        return (0.2, 2.0)
    if (group, key) == ("learning", "learning_rate"):
        return (0.01, 0.12)
    if (group, key) == ("learning", "autonomy_gain"):
        return (0.0, 1.5)
    return (0.0, 1.0)


def version_of(profile: Dict[str, object]) -> str:
    raw = json.dumps(profile, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


class PersonaLearner:
    def __init__(self, profile: Dict[str, object], path: Path | str | None = None,
                 lr: float = 0.02, sigma: float = 0.02, seed: int = 7):
        self.profile = copy.deepcopy(profile)
        if path and Path(path).exists():
            try:
                loaded = json.loads(Path(path).read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded:
                    self.profile = loaded
            except Exception:
                pass
        self.original = copy.deepcopy(self.profile)
        self.path = Path(path) if path else None
        self.lr = float(lr)
        self.sigma = float(sigma)
        self.rng = random.Random(int(seed))
        self.baseline_reward = 0.5
        self.updates = 0
        self.last_delta: Dict[str, float] = {}
        self.epsilon: Dict[str, float] = {}
        self._sample()
        self.version = version_of(self.profile)

    def _key(self, group: str, key: str) -> str:
        return group + "." + key

    def _sample(self) -> None:
        self.epsilon = {}
        for group, key in PATHS:
            self.epsilon[self._key(group, key)] = self.rng.choice((-1.0, 0.0, 1.0)) * self.sigma

    def _clamp(self, group: str, key: str, value: float) -> float:
        lo, hi = _bounds(group, key)
        return clip(float(value), lo, hi)

    def observe_reward(self, reward: float) -> Dict[str, object]:
        r = clip(float(reward), 0.0, 1.0)
        advantage = r - self.baseline_reward
        delta = {}
        for group, key in PATHS:
            k = self._key(group, key)
            eps = self.epsilon.get(k, 0.0)
            if eps == 0.0:
                continue
            old = _get(self.profile, group, key)
            new = self._clamp(group, key, old + self.lr * advantage * eps)
            _put(self.profile, group, key, round(new, 6))
            if abs(new - old) > 1e-6:
                delta[k] = round(new - old, 6)
        self.baseline_reward = 0.9 * self.baseline_reward + 0.1 * r
        self.updates += 1
        self.last_delta = delta
        self.version = version_of(self.profile)
        self._sample()
        self.save()
        return {"version": self.version, "updates": self.updates, "delta": delta,
                "baseline_reward": round(self.baseline_reward, 4)}

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.profile, ensure_ascii=False, indent=1), encoding="utf-8")

    def flat(self) -> Dict[str, object]:
        """Compatibility view for the biological injector."""
        flat = dict(self.profile)
        bf = self.profile.get("big_five") or {}
        for k in ("O", "C", "E", "A", "N"):
            flat[k] = float(bf.get(k, 0.5))
        aff = self.profile.get("affect") or {}
        for k in ("baseline_v", "baseline_a", "stress_reactivity", "recovery_rate"):
            flat[k] = float(aff.get(k, 0.5))
        dr = self.profile.get("drives") or {}
        for k in ("social_baseline", "energy_baseline", "curiosity_baseline", "safety_baseline"):
            flat[k] = float(dr.get(k.replace("_baseline", ""), dr.get(k, 0.5)))
        learn = self.profile.get("learning") or {}
        flat["autonomy_gain"] = float(learn.get("autonomy_gain", 1.0))
        flat["learning_rate"] = float(learn.get("learning_rate", 0.05))
        flat["da_baseline"] = 0.5
        flat["oa_baseline"] = 0.5
        flat["ht_baseline"] = 0.5
        temp = self.profile.get("temperament") or {}
        flat["persona_vector"] = {k: float(v) for k, v in temp.items()}
        meta = self.profile.get("meta") or {}
        flat["voice"] = str(meta.get("voice") or "")
        flat["notes"] = str(meta.get("notes") or "")
        return flat

    def apply_soft(self, mind) -> None:
        """Update the runtime tendency view without resetting session state."""
        flat = self.flat()
        mind.persona_full_profile = copy.deepcopy(self.profile)
        mind.persona_vector = dict(flat.get("persona_vector") or {})
        mind.persona_voice = str(flat.get("voice") or "")
        mind.persona_version = self.version
        try:
            mind.persona_learner = self
        except Exception:
            pass
