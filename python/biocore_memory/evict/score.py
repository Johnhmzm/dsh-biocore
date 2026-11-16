# -*- coding: utf-8 -*-
"""Read-only eviction score (never deletes by itself)."""
from __future__ import annotations
from ..core.config import (EVICT_W_CONFIDENCE, EVICT_W_POSITION,
                           EVICT_W_REDUNDANCY, EVICT_W_SALIENCE,
                           EVICT_W_STALENESS)
from ..core.util import clamp01


def eviction_score(entry, now=0.0, position=0.5, redundancy=0.0):
    e = entry if isinstance(entry, dict) else {}
    salience = clamp01(e.get("importance"), 0.5)
    pos = clamp01(position, 0.5)
    red = clamp01(redundancy, 0.0)
    conf = clamp01(e.get("memory_confidence"), 0.5)
    last = 0.0
    for key in ("last_recall", "last_access", "last_used", "ts"):
        try:
            last = max(last, float(e.get(key) or 0.0))
        except Exception:
            continue
    # now 可能是字符串/None（面板/调用方直接转发）：以前 float(now) 在 try 外面，
    # 坏输入会把"只读打分"整个打挂（P1-6 补测发现）。解析失败按"没有时间参照"处理。
    try:
        stamp = float(now or 0.0)
    except (TypeError, ValueError):
        stamp = 0.0
    age = max(0.0, stamp - last)
    stale = min(1.0, age / 86400.0 / 30.0) if stamp else 0.0
    return round(EVICT_W_SALIENCE * salience + EVICT_W_POSITION * pos
                 + EVICT_W_REDUNDANCY * red
                 + EVICT_W_CONFIDENCE * conf + EVICT_W_STALENESS * stale, 4)
