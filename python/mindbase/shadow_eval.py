from __future__ import annotations

from typing import Dict, List

from . import MindBase, MindConfig
from .persona import Persona
from .relationship_session import RelationshipSession


def replay_numeric(texts: List[str], profile: Dict[str, float], seed: int = 1) -> Dict[str, float]:
    mb = MindBase(MindConfig(seed=seed))
    try:
        Persona.from_profile(profile, seed=seed).apply_to(mb)
    except Exception:
        pass
    s = RelationshipSession(mb)
    for text in texts:
        s.say(str(text), 0.2)
    return {
        "trust": round(float(mb.social.trust_for(s.user)), 4),
        "identity": round(float(mb.self_model.identity), 4),
        "social": round(float(mb.drives.values.get("social", 0.0)), 4),
        "stress": round(float(mb.affect.stress), 4),
        "memory": len(mb.memory.semantic.items) + len(mb.memory.episodic.items),
    }


def shadow_compare(texts: List[str], base: Dict[str, float], delta: Dict[str, float], seed: int = 1) -> Dict[str, object]:
    from .character_profile import merge
    a = replay_numeric(texts, base, seed=seed)
    b = replay_numeric(texts, merge(base, delta), seed=seed)
    return {
        "n": len(texts),
        "base": a,
        "candidate": b,
        "diff": {k: round(float(b[k]) - float(a[k]), 4) for k in a},
    }


def audit_user(base, delta, episodes, n: int = 10) -> dict:
    texts = [str(x.get("text") or "") for x in episodes[-n:] if x.get("text")]
    return shadow_compare(texts, base, delta) if texts else {"n": 0}
