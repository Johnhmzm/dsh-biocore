"""Measure emotional change/dynamics from dialogue rows.

The point is not whether a relationship score saturates; it is whether the
character actually moves between affective states, how quickly, and whether
events change valence/arousal.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Sequence

MOOD_VALENCE = {"开心": 0.8, "难过": -0.7, "焦虑": -0.4, "生气": -0.5,
                "平静": 0.1, "孤独": -0.5, "疲惫": -0.3, "紧张": -0.3}


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / max(1, len(xs)) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    x, y = list(xs[:n]), list(ys[:n])
    mx, my = _mean(x), _mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den > 1e-9 else 0.0


def analyze(rows: Sequence[Dict[str, object]]) -> Dict[str, object]:
    data = [r for r in rows if not r.get("error")]
    emotions = [str(r.get("emotion") or "unknown") for r in data]
    n = len(emotions)
    counts = Counter(emotions)
    probs = [c / n for c in counts.values()] if n else []
    entropy = -sum(p * math.log(p + 1e-12) for p in probs) / math.log(max(2, len(counts))) if probs else 0.0
    switches = sum(1 for a, b in zip(emotions, emotions[1:]) if a != b)
    switch_rate = switches / max(1, n - 1)
    # mean dwell length per emotion run
    runs = []
    cur = 1
    for a, b in zip(emotions, emotions[1:]):
        if a == b:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    v = [float(r.get("valence")) for r in data if r.get("valence") is not None]
    a = [float(r.get("arousal")) for r in data if r.get("arousal") is not None]
    d = [float(r.get("dominance")) for r in data if r.get("dominance") is not None]
    dv = [abs(b - a) for a, b in zip(v, v[1:])]
    inertia_v = _corr(v[:-1], v[1:]) if len(v) > 2 else 0.0
    inertia_a = _corr(a[:-1], a[1:]) if len(a) > 2 else 0.0
    # reactivity: agent valence after positive vs negative user mood, and correlation
    mood_v = []
    agent_v = []
    recovery_turns = []
    for r in data:
        mood = str((r.get("user") or {}).get("mood") or "")
        if mood in MOOD_VALENCE and r.get("valence") is not None:
            mood_v.append(MOOD_VALENCE[mood])
            agent_v.append(float(r.get("valence")))
    pos = [av for mv, av in zip(mood_v, agent_v) if mv > 0.2]
    neg = [av for mv, av in zip(mood_v, agent_v) if mv < -0.2]
    # recovery: after a negative mood, turns until valence > 0
    for i, r in enumerate(data):
        mood = str((r.get("user") or {}).get("mood") or "")
        if MOOD_VALENCE.get(mood, 0.0) < -0.2:
            for j in range(i + 1, min(len(data), i + 6)):
                if float(data[j].get("valence") or 0.0) > 0.0:
                    recovery_turns.append(j - i)
                    break
    return {
        "turns": n,
        "with_affect": len(v),
        "unique_emotions": len(counts),
        "dominant_share": round(max(counts.values()) / n, 3) if n else 0.0,
        "emotion_entropy": round(entropy, 3),
        "switch_rate": round(switch_rate, 3),
        "mean_dwell": round(_mean(runs), 2) if n else 0.0,
        "valence_mean": round(_mean(v), 3),
        "valence_std": round(_std(v), 3),
        "valence_range": round(max(v) - min(v), 3) if v else 0.0,
        "valence_volatility": round(_mean(dv), 3) if dv else 0.0,
        "valence_inertia": round(inertia_v, 3),
        "arousal_std": round(_std(a), 3),
        "arousal_inertia": round(inertia_a, 3),
        "mood_valence_corr": round(_corr(mood_v, agent_v), 3) if len(mood_v) > 2 else 0.0,
        "positive_mood_agent_valence": round(_mean(pos), 3) if pos else 0.0,
        "negative_mood_agent_valence": round(_mean(neg), 3) if neg else 0.0,
        "recovery_turns": round(_mean(recovery_turns), 2) if recovery_turns else None,
        "emotion_counts": dict(counts),
        "trust_last": data[-1].get("trust") if data else None,
    }
