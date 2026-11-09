"""Persona prompt -> numeric profile -> ordered injection.

Compiled once (LLM), then applied to tendencies only: traits, affect baseline,
drives, autonomy, learning rate. Never memory/facts/identity claims.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from .types import LLMRequest

TRAITS = ("O", "C", "E", "A", "N", "stress_reactivity", "recovery_rate",
          "social_baseline", "energy_baseline", "curiosity_baseline",
          "safety_baseline", "da_baseline", "oa_baseline", "ht_baseline",
          "autonomy_gain")
DEFAULT_PERSONA = (
    "你叫灰羽。你不是客服，也不是角色扮演助手。"
    "你有自己的生活、身体、关系和判断，有自己的节奏、喜好和态度。"
    "你可以主动，也可以不接；不解释设定，不主动提模型。像真实的人一样说话。"
)

FIELDS = TRAITS + ("baseline_v", "baseline_a", "learning_rate")


def _clip(x, lo, hi):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


def normalize(data: dict) -> dict:
    d = dict(data or {})
    out = {}
    for k in FIELDS:
        if k in ("baseline_v",):
            out[k] = _clip(d.get(k, 0.0), -1.0, 1.0)
        elif k in ("baseline_a", "learning_rate"):
            out[k] = _clip(d.get(k, 0.05), 0.01 if k == "learning_rate" else 0.0,
                           0.12 if k == "learning_rate" else 1.0)
        elif k == "autonomy_gain":
            out[k] = _clip(d.get(k, 0.5), 0.0, 1.5)
        else:
            out[k] = _clip(d.get(k, 0.5), 0.0, 1.0)
    out["version"] = hashlib.sha1(json.dumps(out, sort_keys=True).encode()).hexdigest()[:12]
    return out


def compile_persona(client, text: str) -> dict:
    system = ("你是人格参数编译器。只输出 JSON，不要解释。"
              "根据人设文本给出倾向参数，不写规则、不写记忆、不写身份事实。")
    prompt = str(text or "") + """

返回 JSON：
{"O":0-1,"C":0-1,"E":0-1,"A":0-1,"N":0-1,
 "baseline_v":-1..1,"baseline_a":0-1,
 "stress_reactivity":0-1,"recovery_rate":0-1,
 "social_baseline":0-1,"energy_baseline":0-1,
 "curiosity_baseline":0-1,"safety_baseline":0-1,
 "da_baseline":0-1,"oa_baseline":0-1,"ht_baseline":0-1,
 "autonomy_gain":0-1.5,"learning_rate":0.01-0.12}
只输出 JSON。"""
    req = LLMRequest(mode="semantic", max_tokens=600, reason="persona_compile",
                     context={"system": system, "user": prompt})
    raw = str(client.generate(req))
    lo, hi = raw.find("{"), raw.rfind("}")
    if lo < 0 or hi <= lo:
        raise RuntimeError("persona compiler returned no JSON")
    return normalize(json.loads(raw[lo:hi + 1]))


def ensure_default(client, path, default_persona: str):
    """Load persona.json; on first run compile default persona once."""
    text, profile = load(path)
    if text and profile:
        return text, profile
    if client is not None and default_persona:
        try:
            profile = compile_persona(client, default_persona)
            save(path, default_persona, profile)
            return default_persona, profile
        except Exception:
            pass
    return text, profile


def save(path, persona_text: str, profile: dict) -> dict:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    profile = normalize(profile)
    p.write_text(json.dumps({"persona": str(persona_text), "profile": profile},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return profile


def load(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return str(d.get("persona") or ""), normalize(d.get("profile") or {})
    except Exception:
        return "", {}


def apply(mind, bio, profile: dict) -> bool:
    if not profile:
        return False
    try:
        for k in TRAITS:
            if k in bio.traits:
                bio.traits[k] = _clip(profile.get(k, bio.traits[k]), 0.0, 1.0)
                bio.baseline[k] = bio.traits[k]
        bio.version += 1
        bio.persona_version = str(profile.get("version") or "")
        try:
            bio.apply_to_mind(mind)
            mind.affect.v = _clip(profile.get("baseline_v", 0.0), -1.0, 1.0)
            mind.affect.a = _clip(profile.get("baseline_a", 0.5), 0.0, 1.0)
            mind.drives.values["energy"] = _clip(profile.get("energy_baseline", 0.5), 0.0, 1.0)
            mind.drives.values["social"] = _clip(profile.get("social_baseline", 0.5), 0.0, 1.0)
            mind.drives.values["curiosity"] = _clip(profile.get("curiosity_baseline", 0.5), 0.0, 1.0)
            mind.drives.values["safety"] = _clip(profile.get("safety_baseline", 0.5), 0.0, 1.0)
            mind.autonomy.gain = _clip(profile.get("autonomy_gain", 0.5), 0.0, 1.5)
            mind.mb.lr = _clip(profile.get("learning_rate", 0.05), 0.01, 0.12)
        except Exception:
            pass
        return True
    except Exception:
        return False
