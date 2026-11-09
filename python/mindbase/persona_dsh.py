"""DSH-compiled persona profile -> biological layer injection.

Order: persona -> emotion -> base.  The original prompt stays semantic; these
numbers only set tendencies.  No request-time LLM parsing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from .noise import clip
from .persona import Persona

PROFILE = Path("mindbase/data/persona_profile_dsh.json")
VECTOR_KEYS = ("warmth", "playfulness", "directness", "verbosity", "formality",
               "teasing", "initiative", "boundary", "emotional", "stability")


def load_profile(path: Path | str = PROFILE) -> Dict[str, object]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_profile(data)


def load_full_profile(path: Path | str = "mindbase/data/persona_profile_full.json") -> Dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_profile(profile: Dict[str, object]) -> Dict[str, object]:
    p = dict(profile)
    for key in ("O", "C", "E", "A", "N", "baseline_a", "stress_reactivity",
                "recovery_rate", "social_baseline", "energy_baseline",
                "curiosity_baseline", "safety_baseline", "da_baseline",
                "oa_baseline", "ht_baseline"):
        p[key] = clip(float(p.get(key, 0.5)), 0.0, 1.0)
    p["baseline_v"] = clip(float(p.get("baseline_v", 0.0)), -1.0, 1.0)
    p["autonomy_gain"] = clip(float(p.get("autonomy_gain", 1.0)), 0.0, 1.5)
    p["learning_rate"] = clip(float(p.get("learning_rate", 0.05)), 0.01, 0.12)
    vec = p.get("persona_vector") or {}
    p["persona_vector"] = {k: clip(float(vec.get(k, 0.5)), 0.0, 1.0) for k in VECTOR_KEYS}
    return p


def version_of(profile: Dict[str, object]) -> str:
    raw = json.dumps(profile, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def snapshot(mind) -> Dict[str, object]:
    snap: Dict[str, object] = {"affect": {}, "drives": {}, "misc": {}}
    for k in ("v", "a", "dominance", "stress", "mood", "da", "oa", "ht"):
        if hasattr(mind.affect, k):
            snap["affect"][k] = float(getattr(mind.affect, k))
    snap["drives"] = dict(getattr(mind.drives, "values", {}))
    for obj, names in ((getattr(mind, "autonomy", None), ("gain",)),
                       (getattr(mind, "will", None), ("willpower",)),
                       (getattr(mind, "mb", None), ("lr",)),
                       (getattr(mind, "social", None), ("trust_gain",)),
                       (getattr(mind, "self_model", None), ("identity", "identity_gain", "identity_setpoint_base"))):
        if obj is None:
            continue
        for name in names:
            if hasattr(obj, name):
                snap["misc"][f"{type(obj).__name__}.{name}"] = float(getattr(obj, name))
    return snap


def rollback(mind, snap: Dict[str, object]) -> None:
    for k, v in (snap.get("affect") or {}).items():
        if hasattr(mind.affect, k):
            setattr(mind.affect, k, float(v))
    for k, v in (snap.get("drives") or {}).items():
        if k in mind.drives.values:
            mind.drives.values[k] = float(v)
    for key, v in (snap.get("misc") or {}).items():
        cls, name = key.split(".", 1)
        for obj in (getattr(mind, "autonomy", None), getattr(mind, "will", None),
                    getattr(mind, "mb", None), getattr(mind, "social", None),
                    getattr(mind, "self_model", None)):
            if obj is not None and type(obj).__name__ == cls and hasattr(obj, name):
                setattr(obj, name, float(v))


def apply_persona(mind, profile: Dict[str, object], seed: int = 0) -> Dict[str, object]:
    """Stages: persona -> emotion -> base, then whitelisted vector effects."""
    profile = validate_profile(profile)
    before = snapshot(mind)
    persona = Persona.from_profile(profile, seed=seed)
    persona.extra.update(profile.get("persona_vector") or {})
    persona.extra["voice"] = str(profile.get("voice") or "")
    persona.extra["persona_version"] = version_of(profile)
    persona.apply_to(mind)
    mind.persona = persona
    mind.persona_numeric = dict(profile)
    mind.persona_vector = dict(profile.get("persona_vector") or {})
    mind.persona_voice = str(profile.get("voice") or "")
    mind.persona_version = version_of(profile)
    vec = mind.persona_vector
    try:
        if hasattr(mind, "autonomy"):
            mind.autonomy.gain = clip(float(mind.autonomy.gain) + 0.20 * (float(vec.get("initiative", 0.5)) - 0.5), 0.0, 1.5)
        if hasattr(mind, "social") and hasattr(mind.social, "trust_gain"):
            mind.social.trust_gain = clip(float(mind.social.trust_gain) + 0.20 * (float(vec.get("warmth", 0.5)) - 0.5), 0.2, 2.0)
        if hasattr(mind, "expression_max_chars"):
            mind.expression_max_chars = int(40 + 260 * float(vec.get("verbosity", 0.5)))
    except Exception:
        pass
    return {"profile": profile, "version": mind.persona_version,
            "before": before, "after": snapshot(mind),
            "voice": mind.persona_voice}


def persona_prompt_suffix(mind) -> str:
    voice = getattr(mind, "persona_voice", "")
    vec = getattr(mind, "persona_vector", {})
    if not voice and not vec:
        return ""
    return ("人设语气：" + voice + "\n风格数值：" +
            ", ".join(f"{k}={v:.2f}" for k, v in vec.items()))
