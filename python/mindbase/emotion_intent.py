"""Emotion layer -> action intent -> expression, then downward biology.

The emotion layer decides what to do (comfort/validate/celebrate/tease/...).
The LLM only decides how the persona says it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

from .emotion_schema import pad_for_emotion
from .noise import clip

INTENTS = ("comfort", "validate", "celebrate", "tease", "protect",
           "boundary", "repair", "withdraw", "curious")


@dataclass
class EmotionIntent:
    intent: str = "comfort"
    tone: str = "warm"
    urgency: float = 0.5
    boundary: float = 0.3
    confidence: float = 0.5
    scores: Dict[str, float] = field(default_factory=dict)


def _softmax(scores: Dict[str, float], temp: float = 0.25) -> Dict[str, float]:
    vals = {k: math.exp(float(v) / max(0.02, temp)) for k, v in scores.items()}
    total = sum(vals.values()) or 1.0
    return {k: v / total for k, v in vals.items()}


def plan_intent(user_emotion: str, affect: Dict[str, float],
                needs: Optional[Dict[str, float]] = None,
                relationship: float = 0.3,
                persona_vector: Optional[Dict[str, float]] = None,
                body: Optional[Dict[str, float]] = None) -> EmotionIntent:
    up, ua, ud = pad_for_emotion(user_emotion)
    iv = float(affect.get("v", 0.0))
    ia = float(affect.get("a", 0.5))
    ist = float(affect.get("stress", 0.0))
    pv = persona_vector or {}
    warmth = float(pv.get("warmth", 0.6))
    play = float(pv.get("playfulness", 0.5))
    emotional = float(pv.get("emotional_expressiveness", 0.5))
    stability = float(pv.get("stability", 0.5))
    deficits = needs or {}
    belong = float(deficits.get("belonging", 0.5))
    safety = float(deficits.get("safety", 0.5))
    b = body or {}
    energy = float(b.get("energy", 0.7))
    fatigue = float(b.get("fatigue", 0.2))
    pain = float(b.get("pain", 0.0))
    warmth = float(b.get("warmth", 0.7))
    scores = {
        "comfort": max(0.0, -up) * 0.9 + warmth * 0.35 + (1.0 - ist) * 0.25 + belong * 0.15,
        "validate": max(0.0, -up) * 0.7 + emotional * 0.25 + ist * 0.20 + 0.10,
        "celebrate": max(0.0, up) * 0.9 + play * 0.35 + stability * 0.10,
        "tease": play * 0.55 + relationship * 0.45 + max(0.0, 1.0 - abs(up)) * 0.20,
        "protect": max(0.0, -up) * 0.5 + (1.0 - ud) * 0.30 + safety * 0.15 + 0.05,
        "boundary": (1.0 - ud) * 0.35 + ist * 0.35 + (0.25 if user_emotion in ("anger", "disgust", "contempt") else 0.0),
        "repair": ist * 0.45 + max(0.0, relationship - 0.3) * 0.25 + 0.05,
        "withdraw": max(0.0, ist - 0.55) * 0.8 + max(0.0, ia - 0.65) * 0.3,
        "curious": max(0.0, up) * 0.2 + max(0.0, ua - 0.4) * 0.3 + warmth * 0.1,
    }
    if user_emotion in ("anger", "disgust", "contempt"):
        scores["boundary"] += 0.55
        scores["withdraw"] += 0.35
        scores["comfort"] *= 0.45
        scores["validate"] *= 0.65
    scores["withdraw"] += 1.10 * max(0.0, fatigue - 0.30) + 0.70 * max(0.0, 0.40 - energy)
    scores["comfort"] *= max(0.30, 1.0 - 0.50 * fatigue)
    scores["protect"] += 0.35 * pain + 0.20 * max(0.0, 0.5 - warmth)
    scores["celebrate"] *= max(0.25, 1.0 - 0.55 * fatigue)
    scores["tease"] *= max(0.30, 1.0 - 0.45 * fatigue - 0.25 * pain)
    scores["comfort"] += 0.15 * max(0.0, 0.5 - warmth)
    probs = _softmax(scores, temp=0.22)
    intent = max(probs, key=probs.get)
    tone_map = {"comfort": "warm", "validate": "warm", "celebrate": "playful",
                "tease": "playful", "protect": "steady", "boundary": "low",
                "repair": "soft", "withdraw": "quiet", "curious": "curious"}
    return EmotionIntent(intent=intent, tone=tone_map.get(intent, "warm"),
                         urgency=round(clip(0.35 + 0.5 * abs(up) + 0.3 * ist, 0.0, 1.0), 3),
                         boundary=round(clip(0.25 + 0.55 * (1.0 - ud), 0.0, 1.0), 3),
                         confidence=round(float(probs[intent]), 3),
                         scores={k: round(v, 3) for k, v in probs.items()})



def plan_intent_affect(user_v: float, user_a: float, user_d: float,
                       affect: Dict[str, float],
                       needs: Optional[Dict[str, float]] = None,
                       relationship: float = 0.3,
                       persona_vector: Optional[Dict[str, float]] = None,
                       body: Optional[Dict[str, float]] = None) -> EmotionIntent:
    """Numeric-only intent planner: input is a PAD reading, not a label."""
    up = max(-1.0, min(1.0, float(user_v)))
    ua = max(0.0, min(1.0, float(user_a)))
    ud = max(-1.0, min(1.0, float(user_d)))
    iv = float(affect.get("v", 0.0))
    ia = float(affect.get("a", 0.5))
    ist = float(affect.get("stress", 0.0))
    pv = persona_vector or {}
    warmth = float(pv.get("warmth", 0.6))
    play = float(pv.get("playfulness", 0.5))
    emotional = float(pv.get("emotional_expressiveness", 0.5))
    stability = float(pv.get("stability", 0.5))
    deficits = needs or {}
    belong = float(deficits.get("belonging", 0.5))
    safety = float(deficits.get("safety", 0.5))
    b = body or {}
    energy = float(b.get("energy", 0.7))
    fatigue = float(b.get("fatigue", 0.2))
    pain = float(b.get("pain", 0.0))
    body_warmth = float(b.get("warmth", 0.7))
    scores = {
        "comfort": max(0.0, -up) * 0.9 + warmth * 0.35 + (1.0 - ist) * 0.25 + belong * 0.15,
        "validate": max(0.0, -up) * 0.7 + emotional * 0.25 + ist * 0.20 + 0.10,
        "celebrate": max(0.0, up) * 0.9 + play * 0.35 + stability * 0.10,
        "tease": play * 0.55 + relationship * 0.45 + max(0.0, 1.0 - abs(up)) * 0.20,
        "protect": max(0.0, -up) * 0.5 + (1.0 - ud) * 0.30 + safety * 0.15 + 0.05,
        "boundary": (1.0 - ud) * 0.35 + ist * 0.35 + (0.55 if (up < -0.35 and ua > 0.55 and ud < 0.1) else 0.0),
        "repair": ist * 0.45 + max(0.0, relationship - 0.3) * 0.25 + 0.05,
        "withdraw": max(0.0, ist - 0.55) * 0.8 + max(0.0, ia - 0.65) * 0.3,
        "curious": max(0.0, up) * 0.2 + max(0.0, ua - 0.4) * 0.3 + warmth * 0.1,
    }
    scores["withdraw"] += 1.10 * max(0.0, fatigue - 0.30) + 0.70 * max(0.0, 0.40 - energy)
    scores["comfort"] *= max(0.30, 1.0 - 0.50 * fatigue)
    scores["protect"] += 0.35 * pain + 0.20 * max(0.0, 0.5 - body_warmth)
    scores["celebrate"] *= max(0.25, 1.0 - 0.55 * fatigue)
    scores["tease"] *= max(0.30, 1.0 - 0.45 * fatigue - 0.25 * pain)
    scores["comfort"] += 0.15 * max(0.0, 0.5 - body_warmth)
    probs = _softmax(scores, temp=0.22)
    intent = max(probs, key=probs.get)
    tone_map = {"comfort": "warm", "validate": "warm", "celebrate": "playful",
                "tease": "playful", "protect": "steady", "boundary": "low",
                "repair": "soft", "withdraw": "quiet", "curious": "curious"}
    return EmotionIntent(intent=intent, tone=tone_map.get(intent, "warm"),
                         urgency=round(clip(0.35 + 0.5 * abs(up) + 0.3 * ist, 0.0, 1.0), 3),
                         boundary=round(clip(0.25 + 0.55 * (1.0 - ud), 0.0, 1.0), 3),
                         confidence=round(float(probs[intent]), 3),
                         scores={k: round(v, 3) for k, v in probs.items()})

def apply_expression_effect(mind, reward: float, ignored: bool = False) -> Dict[str, float]:
    """Downward: speaking and its outcome feed back into biology."""
    r = clip(float(reward), 0.0, 1.0)
    out = {}
    try:
        if ignored:
            mind.affect.stress = clip(mind.affect.stress + 0.04, 0.0, 1.0)
            mind.physiology.cortisol = clip(mind.physiology.cortisol + 0.02, 0.0, 1.0)
            out["stress"] = round(mind.affect.stress, 3)
        elif r >= 0.6:
            mind.affect.stress = clip(mind.affect.stress - 0.015, 0.0, 1.0)
            mind.physiology.oxytocin = clip(mind.physiology.oxytocin + 0.01, 0.0, 1.0)
            out["oxytocin"] = round(mind.physiology.oxytocin, 3)
        elif r < 0.3:
            mind.affect.stress = clip(mind.affect.stress + 0.02, 0.0, 1.0)
            out["stress"] = round(mind.affect.stress, 3)
    except Exception:
        pass
    return out
