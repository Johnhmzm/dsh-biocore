"""Interoception: turn body/affect numbers into a felt first-person state,
and into expression limits. No keyword topic rules; only body thresholds.
"""
from __future__ import annotations

from typing import Dict


def _f(x, default=0.5):
    try:
        return float(x)
    except Exception:
        return default


def felt_state(mind, body: Dict[str, object]) -> str:
    energy = _f(body.get("energy"), 0.7)
    fatigue = _f(body.get("fatigue"), 0.2)
    hydration = _f(body.get("hydration"), 0.7)
    stress = _f(getattr(getattr(mind, "affect", None), "stress", 0.2), 0.2)
    v = _f(getattr(getattr(mind, "affect", None), "v", 0.0), 0.0)
    social = _f((getattr(getattr(mind, "drives", None), "values", {}) or {}).get("social", 0.5), 0.5)
    parts = []
    if fatigue >= 0.70 or energy <= 0.30:
        parts.append("身体很沉，不太想动")
    elif fatigue >= 0.45 or energy <= 0.45:
        parts.append("有点累，动作和话都想省一点")
    elif fatigue >= 0.30 or energy <= 0.65:
        parts.append("稍微有点乏，但还撑得住")
    else:
        parts.append("身上有劲，精神还不错")
    if stress >= 0.70:
        parts.append("心里绷得很紧")
    elif stress >= 0.45:
        parts.append("心里有点紧")
    elif stress >= 0.25:
        parts.append("有点挂着事")
    if hydration <= 0.30:
        parts.append("口干")
    elif hydration <= 0.50:
        parts.append("有点渴")
    if social >= 0.80:
        parts.append("很想找人说说话")
    elif social <= 0.20:
        parts.append("只想安静一会儿")
    if v <= -0.35:
        parts.append("情绪偏低")
    elif v >= 0.35:
        parts.append("情绪是往上走的")
    return "；".join(parts[:4]) + "。"


def expression_limits(mind, body: Dict[str, object]) -> Dict[str, object]:
    energy = _f(body.get("energy"), 0.7)
    fatigue = _f(body.get("fatigue"), 0.2)
    stress = _f(getattr(getattr(mind, "affect", None), "stress", 0.2), 0.2)
    max_tokens = 150
    if fatigue >= 0.30 or energy <= 0.65:
        max_tokens = 110
    if fatigue >= 0.55 or energy <= 0.45:
        max_tokens = 85
    if fatigue >= 0.75 or energy <= 0.30:
        max_tokens = 65
    if stress >= 0.70:
        max_tokens = min(max_tokens, 100)
    style = "短、直接、先接情绪" if max_tokens <= 110 else "可以正常展开，但别长篇"
    return {"max_tokens": int(max_tokens), "style": style}
