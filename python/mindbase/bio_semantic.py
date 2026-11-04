"""Translate biological state into compact semantic intent.

Not raw numbers, not a persona script. Fields separate:
emotion / need / distance / tone / expression / intensity.
"""
from __future__ import annotations


def _f(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return float(default)


def translate(mind, behavior=None, ripple_outputs=None, trust=None, emotion=None, event=None) -> dict:
    b = dict(behavior or {})
    ro = dict(ripple_outputs or {})
    emo = dict(emotion or {})
    try:
        v = _f(getattr(mind.affect, "v", 0.0))
        a = _f(getattr(mind.affect, "a", 0.5))
        stress = _f(getattr(mind.affect, "stress", 0.2))
    except Exception:
        v, a, stress = 0.0, 0.5, 0.2
    try:
        energy = _f(mind.drives.values.get("energy", 0.5))
    except Exception:
        energy = 0.5
    if trust is None:
        try:
            trust = _f(mind.social.trust_for(""), 0.5)
        except Exception:
            trust = 0.5
    trust = _f(trust, 0.5)

    # emotion: keep the strongest 7-dim signal, not only PAD/stress
    dims = {k: _f(emo.get(k), 0.0) for k in
            ("joy", "sadness", "fear", "anger", "surprise", "trust", "loneliness")}
    ev = dict(event or {})
    if ev:
        _v = _f(ev.get("valence"), 0.0)
        if _v < -0.10:
            dims["sadness"] = max(dims["sadness"], min(1.0, -_v * 0.8))
        elif _v > 0.10:
            dims["joy"] = max(dims["joy"], min(1.0, _v * 0.8))
        if _f(ev.get("threat"), 0.0) > 0.15:
            dims["fear"] = max(dims["fear"], min(1.0, _f(ev.get("threat"))))
        if _f(ev.get("connection_delta"), 0.0) > 0.10:
            dims["trust"] = max(dims["trust"], min(1.0, 0.45 + _f(ev.get("connection_delta"))))
    top = max(dims, key=lambda k: dims[k]) if dims else "neutral"
    top_val = dims.get(top, 0.0)
    if top_val < 0.08:
        top = ("sadness" if v < -0.15 else ("joy" if v > 0.15 else "neutral"))
        top_val = abs(v)
    emotion_tag = top if top != "neutral" else "neutral"
    intensity_tag = "high" if max(top_val, abs(v), a) > 0.62 else ("low" if max(top_val, abs(v), a) < 0.28 else "medium")

    energy_tag = "low" if energy < 0.38 else ("high" if energy > 0.72 else "normal")
    stress_tag = "overloaded" if stress > 0.60 else ("tense" if stress > 0.30 else "calm")
    if emotion_tag in ("sadness", "loneliness"):
        need = "be_heard"
    elif emotion_tag in ("fear", "anger"):
        need = "space" if emotion_tag == "fear" else "vent"
    elif emotion_tag in ("joy", "trust"):
        need = "share"
    elif stress_tag == "overloaded" or energy_tag == "low":
        need = "space"
    else:
        need = "talk"

    if emotion_tag in ("fear", "anger", "sadness", "loneliness") or trust < 0.32:
        distance = "far"
    elif emotion_tag in ("joy", "trust") and trust > 0.45:
        distance = "near"
    else:
        distance = "neutral"

    if emotion_tag in ("sadness", "fear"):
        tone = "soft"
    elif emotion_tag == "anger":
        tone = "blunt"
    elif emotion_tag in ("joy", "surprise"):
        tone = "playful"
    elif emotion_tag == "loneliness":
        tone = "quiet"
    else:
        tone = "neutral"

    if stress_tag == "overloaded" or energy_tag == "low":
        intent = "withdraw"
    elif need == "share" and stress_tag != "overloaded":
        intent = "approach"
    elif emotion_tag in ("joy", "trust") and energy_tag != "low":
        intent = "approach"
    elif energy_tag == "high" and trust > 0.62 and stress_tag == "calm":
        intent = "approach"
    elif need == "be_heard" and energy_tag != "low":
        intent = "hold"
    else:
        intent = "hold"
    expression = "short" if intent == "withdraw" or stress_tag == "overloaded" else (
        "open" if intent == "approach" and energy_tag != "low" else "normal")
    q_budget = 1 if (expression == "open" and intent == "approach") or (
        need in ("be_heard", "share") and expression != "short" and energy_tag != "low") else 0
    proactive = 0.0
    if intent == "approach":
        proactive = 0.10 + 0.10 * max(0.0, _f(ro.get("engage_bias")))
    elif intent == "withdraw":
        proactive = -0.15
    mode = str(b.get("private_mode") or "answer")
    if intent == "withdraw" and mode == "answer":
        mode = "brief"
    return {
        "emotion": emotion_tag,
        "intensity": intensity_tag,
        "need": need,
        "distance": distance,
        "tone": tone,
        "intent": intent,
        "energy": energy_tag,
        "stress": stress_tag,
        "expression": expression,
        "question_budget": q_budget,
        "proactive_bias": round(proactive, 3),
        "mode": mode,
        "valence": "positive" if v > 0.12 else ("negative" if v < -0.12 else "neutral"),
    }


def context(semantic: dict) -> str:
    s = dict(semantic or {})
    return ("emotion=%s intensity=%s need=%s distance=%s tone=%s "
            "intent=%s expression=%s mode=%s" % (
                s.get("emotion", "neutral"), s.get("intensity", "medium"),
                s.get("need", "talk"), s.get("distance", "neutral"),
                s.get("tone", "neutral"), s.get("intent", "hold"),
                s.get("expression", "normal"), s.get("mode", "answer")))
