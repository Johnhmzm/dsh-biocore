# -*- coding: utf-8 -*-
"""MemoryOS-style heat score (pure; default OFF)."""
from __future__ import annotations

import math
import time

from ..core.util import clamp01

HEAT_ENABLED = False
ALPHA = 1.0
BETA = 1.0
GAMMA = 1.0
TAU_HOURS = 24.0


def _num(entry, keys):
    # 只用**存在且能解析**的键：缺键时以前也会成功返回 0.0，于是后面的别名
    # （hits / N_visit / L_interaction）永远读不到（P1-6 补测发现）。
    for key in keys:
        value = entry.get(key) if isinstance(entry, dict) else None
        if value is None:
            continue
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    return 0.0


def _last_ts(entry):
    for key in ("last_recall", "last_access", "last_visit_time", "ts", "created_ts"):
        try:
            value = float(entry.get(key) or 0.0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0.0


def heat(entry, now=None):
    """heat = alpha*visits + beta*interactions + gamma*recency, normalized 0..1."""
    if not HEAT_ENABLED or not isinstance(entry, dict):
        return 0.0
    try:
        stamp = float(now if now is not None else time.time())
        visits = _num(entry, ("visits", "visit_count", "N_visit", "hits"))
        interactions = _num(entry, ("interactions", "interaction_count", "L_interaction"))
        last = _last_ts(entry)
        age_hours = max(0.0, stamp - last) / 3600.0 if last else 0.0
        recency = 0.5 ** (age_hours / TAU_HOURS)
        v = visits / (1.0 + visits)
        i = interactions / (1.0 + interactions)
        total = ALPHA + BETA + GAMMA
        return clamp01((ALPHA * v + BETA * i + GAMMA * recency) / total, 0.0)
    except Exception:
        return 0.0
