"""Memory -> biological layer trace.

A recalled memory is not just prompt text:
its valence / importance / open-loop pressure
nudges the current affect state.
"""
from __future__ import annotations

from typing import Dict


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def apply_memory_trace(mind, trace: Dict[str, object], strength: float = 0.06) -> Dict[str, object]:
    if not trace:
        return {}
    v = _clip(float(trace.get("valence", 0.0)), -1.0, 1.0)
    a = _clip(float(trace.get("arousal", 0.0)), 0.0, 1.0)
    imp = _clip(float(trace.get("importance", 0.0)), 0.0, 1.0)
    neg = _clip(float(trace.get("negative_ratio", 0.0)), 0.0, 1.0)
    loops = int(trace.get("open_loops", 0) or 0)
    k = _clip(float(strength), 0.0, 0.5) * (0.5 + 0.5 * imp)
    if abs(v) < 1e-6 and a < 1e-6 and loops == 0:
        return {}
    res = mind.inject_affect(v, a, dominance=0.35 * v,
                             intensity=k, blend=1.0)
    try:
        if loops:
            extra = min(0.03, 0.006 * loops) * max(0.2, imp)
            mind.affect.stress = _clip(float(mind.affect.stress) + extra, 0.0, 1.0)
            res["stress"] = round(float(mind.affect.stress), 4)
        ph = mind.physiology
        if v < 0:
            ph.cortisol = _clip(float(ph.cortisol) + k * neg * 0.03, 0.0, 1.0)
            res["cortisol"] = round(float(ph.cortisol), 4)
        elif v > 0:
            ph.oxytocin = _clip(float(ph.oxytocin) + k * (1.0 - neg) * 0.025, 0.0, 1.0)
            res["oxytocin"] = round(float(ph.oxytocin), 4)
    except Exception:
        pass
    res.update({"memory_valence": round(v, 4), "memory_importance": round(imp, 4),
                "memory_negative_ratio": round(neg, 4), "memory_open_loops": loops,
                "memory_strength": round(k, 4)})
    return res
