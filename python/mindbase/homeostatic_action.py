"""Homeostatic action selection: needs pick self-care behavior.

No keyword rules. Deficits and body variables are compared numerically.
"""
from __future__ import annotations

import time
from typing import Dict, Optional


def choose_self_care(body, mind=None, now: float = 0.0,
                     last_action: float = 0.0, cooldown_s: float = 120.0) -> Optional[str]:
    now = float(now or time.time())
    if now - float(last_action or 0.0) < float(cooldown_s):
        return None
    e = float(getattr(body, "energy", 0.7))
    h = float(getattr(body, "hydration", 0.7))
    f = float(getattr(body, "fatigue", 0.2))
    w = float(getattr(body, "warmth", 0.7))
    debt = float(getattr(body, "sleep_debt", 0.1))
    needs = {}
    try:
        needs = dict(getattr(getattr(mind, "needs", None), "deficits", {}) or {})
    except Exception:
        pass
    phys = float(needs.get("physiological", 0.0) or 0.0)
    safety = float(needs.get("safety", 0.0) or 0.0)
    scores = {
        "drink": (0.52 - h) * 1.8 + phys * 0.3,
        "eat": (0.52 - e) * 1.5 + phys * 0.2,
        "rest": (f - 0.55) * 1.8 + debt * 0.5,
        "sleep": (debt - 0.65) * 2.0 + (f - 0.70) * 0.8,
        "warm": (0.55 - w) * 1.6 + safety * 0.2,
    }
    action, score = max(scores.items(), key=lambda kv: kv[1])
    if score < 0.12:
        return None
    return action
