from __future__ import annotations

from typing import Dict, List

from . import MindBase, MindConfig
from .llm_adapter import LLMAdapter
from .persona import Persona
from .relationship_session import RelationshipSession


def evaluate_profile(texts: List[str], profile: Dict[str, float], client, persona_text: str = "", seed: int = 1, apply: bool = True, reps: int = 3) -> Dict[str, object]:
    import statistics
    reps_list = []
    for i in range(max(1, int(reps))):
        reps_list.append(_evaluate_once(texts, profile, client, persona_text, seed + i, apply))
    keys = ("reply_len_mean", "trust", "identity")
    out = {"n": len(texts), "reps": len(reps_list)}
    for k in keys:
        vals = [float(r[k]) for r in reps_list]
        out[k] = round(sum(vals) / len(vals), 4)
        out[k + "_sd"] = round(statistics.pstdev(vals), 4)
    return out


def _evaluate_once(texts: List[str], profile: Dict[str, float], client, persona_text: str = "", seed: int = 1, apply: bool = True) -> Dict[str, object]:
    mb = MindBase(MindConfig(seed=seed))
    if apply:
        try:
            Persona.from_profile(profile, seed=seed).apply_to(mb)
        except Exception:
            pass
    sess = RelationshipSession(mb, adapter=LLMAdapter(mb, client), semantic=True)
    rows = []
    for text in texts:
        rows.append(sess.say(str(text), 0.2))
    n = max(1, len(rows))
    lengths = [len(str(r.get("reply") or "")) for r in rows]
    return {
        "n": len(rows),
        "reply_len_mean": round(sum(lengths) / n, 2),
        "trust": round(float(mb.social.trust_for(sess.user)), 4),
        "identity": round(float(mb.self_model.identity), 4),
        "turns": len(sess.turns),
    }


def compare_profiles(texts: List[str], base: Dict[str, float], candidate: Dict[str, float], client, persona_text: str = "", seed: int = 1) -> Dict[str, object]:
    a = evaluate_profile(texts, base, client, persona_text, seed=seed)
    b = evaluate_profile(texts, candidate, client, persona_text, seed=seed)
    return {
        "base": a,
        "candidate": b,
        "diff": {k: round(float(b[k]) - float(a[k]), 4) for k in ("reply_len_mean", "trust", "identity")},
    }


def compare_default(texts: List[str], candidate: Dict[str, float], client, persona_text: str = "", seed: int = 1, reps: int = 3) -> Dict[str, object]:
    a = evaluate_profile(texts, {}, client, persona_text, seed=seed, apply=False, reps=reps)
    b = evaluate_profile(texts, candidate, client, persona_text, seed=seed, apply=True, reps=reps)
    diff = {}
    for k in ("reply_len_mean", "trust", "identity"):
        diff[k] = round(float(b[k]) - float(a[k]), 4)
        diff[k + "_sd"] = round((float(a.get(k + "_sd", 0.0)) ** 2 + float(b.get(k + "_sd", 0.0)) ** 2) ** 0.5, 4)
    return {"default": a, "candidate": b, "diff": diff}
