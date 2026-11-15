from __future__ import annotations

import hashlib
from typing import Dict, List, Sequence

from ..lexicon import text_affect
from ..noise import clip

STATE_KEYS = (
    "stress",
    "mood",
    "v",
    "a",
    "social",
    "energy",
    "safety",
    "curiosity",
    "identity",
    "willpower",
    "confidence",
)
TEXT_BUCKETS = 16
STRUCT_KEYS = (
    "ev_a", "ev_b", "var_a", "var_b",
    "max_loss_a", "max_loss_b", "p_win_a", "p_win_b",
    "ev_gap", "var_gap", "max_loss_gap", "p_win_gap",
)


def state_vector(state: Dict[str, float]) -> List[float]:
    return [clip(float(state.get(k, 0.0)), -1.0, 1.0) for k in STATE_KEYS]


def _bucket(ch: str) -> int:
    return hashlib.md5(ch.encode("utf-8")).digest()[0] % TEXT_BUCKETS


def event_features(text: str) -> List[float]:
    text = text or ""
    buckets = [0.0] * TEXT_BUCKETS
    for ch in text:
        buckets[_bucket(ch)] += 1.0
    scale = max(1.0, float(len(text)))
    buckets = [v / scale for v in buckets]
    return [
        min(1.0, len(text) / 80.0),
        1.0 if "？" in text or "?" in text else 0.0,
        1.0 if "！" in text or "!" in text else 0.0,
        1.0 if "老板" in text else 0.0,
    ] + buckets


def structured_vector(structured: Dict[str, float] | None) -> List[float]:
    data = structured or {}
    out: List[float] = []
    for k in STRUCT_KEYS:
        value = float(data.get(k, 0.0))
        if k.startswith("p_win"):
            out.append(clip(value, 0.0, 1.0))
        else:
            out.append(clip(value / 100.0, -1.0, 1.0))
    return out


def affect_features(text: str) -> List[float]:
    v, a = text_affect(text or "")
    return [float(v), float(a), 1.0 if v < 0.0 else 0.0, 1.0 if v > 0.0 else 0.0]


def sample_vector(event: str, state: Dict[str, float], structured: Dict[str, float] | None = None) -> List[float]:
    return state_vector(state) + event_features(event) + structured_vector(structured) + affect_features(event)


def feature_names() -> List[str]:
    return list(STATE_KEYS) + ["len", "question", "excite", "boss"] + [f"text_{i}" for i in range(TEXT_BUCKETS)] + list(STRUCT_KEYS) + ["aff_v", "aff_a", "aff_neg", "aff_pos"]
