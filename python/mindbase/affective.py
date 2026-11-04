"""Biological affect injection and empathy hooks.

Emotions can be injected as internal states (not text labels).  A user-emotion
signal can be mirrored with a learnable empathy gain.  The base remains the
source of state; the LLM only expresses it.
"""
from __future__ import annotations

from typing import Dict, Tuple

from .noise import clip
from .emotion_schema import PAD_PROTOTYPES, pad_for_emotion, emotion_readout


# prototype affect targets: valence, arousal, stress, oxytocin, cortisol
EMOTION_PROFILES: Dict[str, Dict[str, float]] = {
    "joy": {"v": 0.8, "a": 0.6, "stress": -0.2, "oxy": 0.2, "cortisol": -0.1},
    "sadness": {"v": -0.7, "a": 0.25, "stress": 0.3, "oxy": -0.1, "cortisol": 0.1},
    "fear": {"v": -0.6, "a": 0.8, "stress": 0.7, "oxy": -0.1, "cortisol": 0.3},
    "anger": {"v": -0.5, "a": 0.75, "stress": 0.6, "oxy": -0.1, "cortisol": 0.2},
    "surprise": {"v": 0.1, "a": 0.8, "stress": 0.2, "oxy": 0.0, "cortisol": 0.0},
    "disgust": {"v": -0.6, "a": 0.5, "stress": 0.4, "oxy": -0.1, "cortisol": 0.1},
    "love": {"v": 0.8, "a": 0.4, "stress": -0.2, "oxy": 0.4, "cortisol": -0.1},
    "loneliness": {"v": -0.5, "a": 0.3, "stress": 0.4, "oxy": -0.2, "cortisol": 0.1},
    "calm": {"v": 0.4, "a": 0.2, "stress": -0.4, "oxy": 0.2, "cortisol": -0.2},
    "anxiety": {"v": -0.4, "a": 0.7, "stress": 0.6, "oxy": -0.1, "cortisol": 0.2},
    "shame": {"v": -0.5, "a": 0.4, "stress": 0.5, "oxy": -0.1, "cortisol": 0.2},
    "guilt": {"v": -0.4, "a": 0.4, "stress": 0.4, "oxy": 0.0, "cortisol": 0.15},
    "pride": {"v": 0.6, "a": 0.5, "stress": -0.1, "oxy": 0.1, "cortisol": 0.0},
    "gratitude": {"v": 0.7, "a": 0.35, "stress": -0.2, "oxy": 0.3, "cortisol": -0.1},
    "hope": {"v": 0.5, "a": 0.45, "stress": -0.1, "oxy": 0.1, "cortisol": 0.0},
    "boredom": {"v": -0.2, "a": 0.15, "stress": 0.1, "oxy": -0.1, "cortisol": 0.0},
}


def inject_emotion(mind, emotion: str, intensity: float = 0.5, blend: float = 0.6) -> Dict[str, object]:
    """Inject a biological emotion into affect/physiology, not just a label."""
    name = str(emotion).lower()
    prof = EMOTION_PROFILES.get(name)
    if prof is None:
        return {"injected": None, "emotion": name}
    k = clip(float(intensity), 0.0, 1.0) * clip(float(blend), 0.0, 1.0)
    a = mind.affect
    pad = pad_for_emotion(name)
    a.v = clip(a.v + k * (pad[0] - a.v), -1.0, 1.0)
    a.a = clip(a.a + k * (pad[1] - a.a), 0.0, 1.0)
    if hasattr(a, "dominance"):
        a.dominance = clip(a.dominance + k * (pad[2] - a.dominance), -1.0, 1.0)
    a.stress = clip(a.stress + k * (prof["stress"] - a.stress), 0.0, 1.0)
    try:
        p = mind.physiology
        p.oxytocin = clip(p.oxytocin + k * prof["oxy"], 0.0, 1.0)
        p.cortisol = clip(p.cortisol + k * prof["cortisol"], 0.0, 1.0)
    except Exception:
        pass
    mind.last_injected_emotion = {"emotion": name, "intensity": round(k, 4)}
    return {"injected": name, "intensity": round(k, 4),
            "affect": {"v": round(a.v, 4), "a": round(a.a, 4),
                       "dominance": round(float(getattr(a, "dominance", 0.0)), 4),
                       "stress": round(a.stress, 4)}}


def empathize(mind, user_emotion: str, intensity: float = 0.5,
              empathy_gain: float = 0.35) -> Dict[str, object]:
    """Mirror a user emotion with a learnable gain; not a hard rule."""
    return inject_emotion(mind, user_emotion, intensity=float(intensity) * float(empathy_gain), blend=0.7)


def perceive_emotion_lexicon(text: str) -> Tuple[str, float]:
    """Small deterministic fallback sensor; an LLM/classifier can replace it."""
    t = str(text or "")
    table = [
        ("sadness", ("难过", "伤心", "累", "崩溃", "哭", "失落")),
        ("anxiety", ("焦虑", "紧张", "担心", "害怕", "慌")),
        ("anger", ("生气", "愤怒", "烦", "滚", "讨厌")),
        ("joy", ("开心", "高兴", "笑", "太好了", "喜欢")),
        ("loneliness", ("孤独", "没人", "一个人", "寂寞")),
        ("love", ("爱你", "想你", "抱抱", "亲亲")),
        ("gratitude", ("谢谢", "感谢", "麻烦你")),
    ]
    for emotion, words in table:
        for w in words:
            if w in t:
                return emotion, 0.6
    return "neutral", 0.0


def inject_affect(mind, valence: float, arousal: float, dominance: float = 0.0,
                  intensity: float = 0.8, blend: float = 0.7) -> Dict[str, object]:
    """Inject a numeric PAD reading. No categorical label is involved."""
    v = max(-1.0, min(1.0, float(valence)))
    a = max(0.0, min(1.0, float(arousal)))
    d = max(-1.0, min(1.0, float(dominance)))
    k = clip(float(intensity), 0.0, 1.0) * clip(float(blend), 0.0, 1.0)
    # negativity bias: negative events have a stronger pull than positive ones
    k *= 1.25 if v < 0 else 0.90
    aff = mind.affect
    aff.v = clip(aff.v + k * (v - aff.v), -1.0, 1.0)
    aff.a = clip(aff.a + k * (a - aff.a), 0.0, 1.0)
    if hasattr(aff, "dominance"):
        aff.dominance = clip(aff.dominance + k * (d - aff.dominance), -1.0, 1.0)
    aff.stress = clip(aff.stress + k * max(0.0, -v) * 0.45, 0.0, 1.0)
    try:
        p = mind.physiology
        p.oxytocin = clip(p.oxytocin + k * max(0.0, v) * 0.03, 0.0, 1.0)
        p.cortisol = clip(p.cortisol + k * max(0.0, -v) * 0.03, 0.0, 1.0)
    except Exception:
        pass
    return {"valence": round(aff.v, 4), "arousal": round(aff.a, 4),
            "dominance": round(float(getattr(aff, "dominance", 0.0)), 4),
            "stress": round(aff.stress, 4)}
