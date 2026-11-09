"""Cheap social-addressing inference.

This is not a semantic world model. It only answers:
"is this message plausibly aimed at her?"
Explicit @/name is handled by the bridge; this fills the implicit gap:
continuations after her reply, second-person questions, backchannels.
"""
from __future__ import annotations

import re

ALIASES = ("灰羽", "能天使", "阿能", "小乐", "蕾缪乐")
_AT = re.compile(r"@[^\s(（]+")
_SECOND_PERSON = ("你", "您")
_QUESTION = ("吗", "呢", "怎么", "为什么", "啥", "什么", "哪", "谁", "吗？", "?", "？")
_CONTINUE = ("然后", "所以", "那", "可是", "但是", "刚才", "继续", "你说", "你觉得", "你看")


def infer_addressing(rows, text: str, speaker: str = "") -> dict:
    t = str(text or "").strip()
    low = t.lower()
    score = 0.0
    reasons = []
    for a in ALIASES:
        if a in t:
            score = 1.0
            reasons.append("name:" + a)
            break
    if score >= 1.0:
        return {"score": 1.0, "addressed": True, "reasons": reasons}
    ats = _AT.findall(t)
    if ats:
        # @ someone else is a strong negative unless an alias is also present
        score -= 0.35
        reasons.append("at_other")
    prev = {}
    try:
        if len(rows) >= 2:
            prev = rows[-2]
    except Exception:
        prev = {}
    prev_reply = str(prev.get("reply") or "").strip() if isinstance(prev, dict) else ""
    if prev_reply:
        score += 0.35
        reasons.append("after_her_reply")
    if any(w in t for w in _SECOND_PERSON):
        score += 0.20
        reasons.append("second_person")
    if any(w in t for w in _QUESTION):
        score += 0.15
        reasons.append("question")
    if any(w in t for w in _CONTINUE):
        score += 0.12
        reasons.append("continuation")
    if len(t) <= 8 and t and not ats:
        score += 0.08
        reasons.append("short")
    # previous row spoken by someone else, current speaker is a different person:
    # this is often a dyadic exchange, not aimed at her.
    try:
        if len(rows) >= 2:
            psp = str(rows[-2].get("speaker") or "")
            if psp and speaker and psp != speaker:
                score -= 0.10
                reasons.append("other_speaker")
    except Exception:
        pass
    score = max(0.0, min(1.0, score))
    return {"score": round(score, 3), "addressed": bool(score >= 0.55), "reasons": reasons}
