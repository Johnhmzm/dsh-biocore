"""Numeric reply gate, initially shadow mode.

Decides whether a direct chat turn is worth an LLM call. It uses only local
features and state; no extra LLM call. Shadow mode logs p_reply but always
lets the turn through. Set LLM_GATE_ENFORCE=1 to enable gating for non-forced
turns.
"""
from __future__ import annotations

import os
import re


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


class ReplyGate:
    def __init__(self, threshold: float = 0.55):
        self.threshold = float(threshold)
        self.enforce = os.environ.get("LLM_GATE_ENFORCE", "0") == "1"

    def evaluate(self, text: str, mind, trust: float = 0.5, now: float = 0.0,
                 last_user_ts: float = 0.0, addressed: bool = True,
                 force_addressed: bool = False) -> dict:
        t = str(text or "")
        low = t.lower()
        question = bool(re.search(r"[?？]|吗|么|呢|怎么|为什么|多少|哪|谁|能不能|可不可以|帮我", t))
        call = any(w in t for w in ("能天使", "阿能", "小乐", "蕾缪乐", "诺登"))
        safety = any(w in t for w in ("自杀", "不想活", "报警", "救命", "受伤", "疼", "医院"))
        salience = _clip(0.35 + 0.25 * min(1.0, len(t) / 80.0) + (0.20 if question else 0.0))
        aff = getattr(mind, "affect", None)
        arousal = _clip(getattr(aff, "a", 0.4) if aff else 0.4)
        stress = _clip(getattr(aff, "stress", 0.2) if aff else 0.2)
        drives = getattr(getattr(mind, "drives", None), "values", {}) or {}
        social = _clip(drives.get("social", 0.5))
        energy = _clip(drives.get("energy", 0.5))
        recency = 0.0
        if last_user_ts and now:
            recency = _clip(1.0 - (float(now) - float(last_user_ts)) / 600.0)
        brevity = _clip(1.0 - len(t) / 40.0)
        p = _clip(0.30 * salience + 0.25 * arousal + 0.15 * social +
                  0.10 * (1.0 - stress) + 0.10 * energy + 0.05 * trust + 0.05 * recency
                  - 0.20 * brevity)
        force = bool(question or call or safety or force_addressed)
        return {"p_reply": round(p, 4), "threshold": self.threshold,
                "force": force, "question": question, "call": call, "safety": safety,
                "salience": round(salience, 4), "brevity": round(brevity, 4), "enforce": self.enforce,
                "would_reply": bool(force or p >= self.threshold)}
