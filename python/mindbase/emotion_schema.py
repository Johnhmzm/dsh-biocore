"""Canonical multi-dimensional emotion schema.

PAD (pleasure/arousal/dominance) + appraisal + discrete taxonomy.
Prototypes are approximate numeric anchors; they are learnable parameters,
not claims of universal biological constants.
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

# name: (pleasure [-1,1], arousal [0,1], dominance [-1,1])
PAD_PROTOTYPES: Dict[str, Tuple[float, float, float]] = {
    "joy": (0.8, 0.60, 0.4),
    "sadness": (-0.7, 0.25, -0.4),
    "fear": (-0.6, 0.80, -0.6),
    "anger": (-0.5, 0.75, 0.2),
    "disgust": (-0.6, 0.50, 0.0),
    "surprise": (0.1, 0.80, -0.2),
    "love": (0.8, 0.40, 0.3),
    "trust": (0.6, 0.30, 0.2),
    "anticipation": (0.3, 0.50, 0.3),
    "loneliness": (-0.5, 0.30, -0.5),
    "anxiety": (-0.4, 0.70, -0.5),
    "shame": (-0.5, 0.40, -0.7),
    "guilt": (-0.4, 0.40, -0.4),
    "pride": (0.6, 0.50, 0.7),
    "gratitude": (0.7, 0.35, 0.2),
    "hope": (0.5, 0.45, 0.4),
    "calm": (0.4, 0.20, 0.5),
    "boredom": (-0.2, 0.15, -0.3),
    "contempt": (-0.3, 0.40, 0.6),
    "envy": (-0.3, 0.50, -0.4),
    "jealousy": (-0.3, 0.60, -0.3),
    "awe": (0.3, 0.70, -0.2),
    "amusement": (0.7, 0.50, 0.4),
    "relief": (0.5, 0.20, 0.3),
    "distress": (-0.6, 0.60, -0.5),
    "neutral": (0.0, 0.50, 0.0),
}

# canonical taxonomy mappings (approximate)
EKMAN = {
    "joy": "joy", "sadness": "sadness", "fear": "fear", "anger": "anger",
    "disgust": "disgust", "surprise": "surprise",
    "anxiety": "fear", "loneliness": "sadness", "shame": "sadness",
    "guilt": "sadness", "distress": "sadness", "love": "joy",
    "pride": "joy", "gratitude": "joy", "hope": "joy", "calm": "joy",
}
PLUTCHIK = {
    "joy": "joy", "trust": "trust", "fear": "fear", "surprise": "surprise",
    "sadness": "sadness", "disgust": "disgust", "anger": "anger",
    "anticipation": "anticipation", "love": "trust", "anxiety": "fear",
    "loneliness": "sadness", "shame": "sadness", "guilt": "sadness",
    "pride": "joy", "gratitude": "trust", "hope": "anticipation",
    "calm": "trust", "contempt": "anger", "envy": "anger",
    "jealousy": "anger", "awe": "surprise", "boredom": "sadness",
}


def pad_for_emotion(emotion: str) -> Tuple[float, float, float]:
    return PAD_PROTOTYPES.get(str(emotion).lower(), PAD_PROTOTYPES["neutral"])


def taxonomy(emotion: str) -> Dict[str, str]:
    name = str(emotion).lower()
    return {"ekman": EKMAN.get(name, name), "plutchik": PLUTCHIK.get(name, name)}


def pad_distance(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    # P and D are in [-1,1], A in [0,1]
    return (0.5 * abs(float(a[0]) - float(b[0]))
            + 1.0 * abs(float(a[1]) - float(b[1]))
            + 0.5 * abs(float(a[2]) - float(b[2])))


def emotion_readout(pleasure: float, arousal: float, dominance: float,
                    temperature: float = 0.25) -> Dict[str, float]:
    """Multi-label intensity distribution over canonical emotions."""
    p = max(-1.0, min(1.0, float(pleasure)))
    a = max(0.0, min(1.0, float(arousal)))
    d = max(-1.0, min(1.0, float(dominance)))
    vals = []
    for name, proto in PAD_PROTOTYPES.items():
        dist = pad_distance((p, a, d), proto)
        vals.append((math.exp(-dist / max(0.02, float(temperature))), name))
    total = sum(v for v, _ in vals) or 1.0
    return {name: round(v / total, 5) for v, name in vals}


def top_emotions(readout: Dict[str, float], k: int = 5) -> list:
    return sorted(readout.items(), key=lambda x: x[1], reverse=True)[:max(1, int(k))]
