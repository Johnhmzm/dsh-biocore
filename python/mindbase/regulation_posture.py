"""Deterministic regulation posture, inspired by Gubernaut.

Meta-level reads only numeric telemetry: intensity, valence, repetition.
It returns a posture; no token is injected into the decision.
"""
from __future__ import annotations


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


class RegulationPosture:
    def evaluate(self, intensity: float, valence: float, repetition: float,
                 stress: float = 0.0) -> dict:
        i = _clip(intensity)
        v = max(-1.0, min(1.0, float(valence)))
        r = _clip(repetition)
        s = _clip(stress)
        if r >= 0.65 and v < 0.0:
            posture = "withdraw"
        elif i >= 0.70 and v <= -0.15:
            posture = "deescalate"
        elif i >= 0.70 and s >= 0.60:
            posture = "hold"
        elif v <= -0.45:
            posture = "withdraw"
        elif i >= 0.65:
            posture = "engage_brief"
        elif v >= 0.25:
            posture = "engage"
        else:
            posture = "hold"
        return {"posture": posture, "intensity": round(i, 4), "valence": round(v, 4),
                "repetition": round(r, 4), "stress": round(s, 4)}

    def guidance(self, d: dict) -> str:
        p = d.get("posture")
        if p == "withdraw":
            return "调节姿态=withdraw；话少、别开新话题、别追问；只回必要的一句。"
        if p == "deescalate":
            return "调节姿态=deescalate；降低强度、短句、不接挑衅、不升级冲突。"
        if p == "hold":
            return "调节姿态=hold；保持当前状态，不要主动加码。"
        if p == "engage_brief":
            return "调节姿态=engage_brief；可以接，但别长篇、别连续追问。"
        return "调节姿态=engage；正常回应，保持自然。"
