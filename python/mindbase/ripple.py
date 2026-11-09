"""Emotional ripple field: a touch on one dimension propagates to coupled ones.

A reply/bite emits an impulse; the field advances one step, so adjacent
dimensions receive delayed, attenuated echoes. This is not a claim of
subjective feeling; it is a causal state carrier.
"""
from __future__ import annotations

import math

from .noise import clip

DIMS = ("joy", "sadness", "fear", "anger", "surprise", "trust", "loneliness", "stress")

# source -> list of (target, weight)
COUPLING = {
    "joy": [("trust", 0.25), ("stress", -0.15)],
    "sadness": [("loneliness", 0.35), ("trust", -0.15), ("stress", 0.15)],
    "fear": [("stress", 0.30), ("surprise", 0.10), ("trust", -0.20)],
    "anger": [("stress", 0.25), ("fear", 0.15), ("trust", -0.20)],
    "surprise": [("fear", 0.15), ("joy", 0.10), ("stress", 0.10)],
    "trust": [("joy", 0.20), ("fear", -0.10), ("loneliness", -0.15)],
    "loneliness": [("sadness", 0.30), ("trust", -0.10)],
    "stress": [("fear", 0.15), ("sadness", 0.15), ("joy", -0.15)],
}

# spatial coordinates: x, y, z(layer depth 0=lower, 1=upper)
POS = {
    "joy": (0.0, 1.0, 0.8),
    "sadness": (0.0, -1.0, 0.3),
    "trust": (1.0, 0.0, 0.65),
    "loneliness": (-1.0, 0.0, 0.25),
    "fear": (0.7, 0.3, 0.1),
    "anger": (-0.7, -0.3, 0.2),
    "stress": (0.0, -0.7, 0.05),
    "surprise": (0.5, 0.5, 0.9),
}

def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))

_KEYWORDS = {
    "开心": ("joy",), "高兴": ("joy",), "喜欢": ("joy", "trust"), "爱": ("joy", "trust"),
    "难过": ("sadness",), "伤心": ("sadness",), "失望": ("sadness",), "委屈": ("sadness",),
    "怕": ("fear",), "害怕": ("fear",), "焦虑": ("fear", "stress"), "紧张": ("fear", "stress"),
    "生气": ("anger",), "烦": ("anger", "stress"), "讨厌": ("anger",), "滚": ("anger",),
    "累": ("stress",), "压力": ("stress",), "加班": ("stress",), "忙": ("stress",),
    "孤独": ("loneliness",), "没人": ("loneliness",), "一个人": ("loneliness",),
    "谢谢": ("trust", "joy"), "陪": ("trust",), "相信": ("trust",),
}


class RippleField:
    def __init__(self, decay: float = 0.88, gain: float = 0.12, cap: float = 0.30):
        self.decay = float(decay)
        self.gain = float(gain)
        self.cap = float(cap)
        self.values = {d: 0.0 for d in DIMS}
        self.last_touch = {}
        self.last_step = 0.0
        self.last_scale = 1.0
        self.waves = []
        self.layer_upper = 0.0
        self.layer_lower = 0.0
        self.layer_overlap = 0.0

    def touch(self, dim: str, amount: float) -> None:
        if dim not in self.values:
            return
        self.values[dim] = clip(self.values[dim] + float(amount), -self.cap, self.cap)
        self.last_touch[dim] = self.last_touch.get(dim, 0.0) + float(amount)
        if abs(float(amount)) > 1e-6:
            self.waves.append({"pos": POS.get(dim, (0.0, 0.0, 0.5)),
                               "amp": float(amount), "age": 0.0, "sigma": 0.55})

    def emit(self, text: str = "", affect=None, injection=None) -> dict:
        t = str(text or "")
        added = {}
        try:
            v0 = abs(float(getattr(affect, "v", 0.0))) if affect is not None else 0.0
        except Exception:
            v0 = 0.0
        inj = dict(injection or {})
        sev = max(v0, float(inj.get("threat") or 0.0), float(inj.get("stress") or 0.0),
                  float(inj.get("guilt") or 0.0), float(inj.get("shame") or 0.0),
                  abs(float(inj.get("connection_delta") or 0.0)))
        hits = sum(1 for key in _KEYWORDS if key in t)
        sev = min(1.0, max(sev, 0.10 * min(3, hits)))
        scale = max(0.35, min(1.8, 0.35 + 1.25 * sev))
        self.last_scale = scale
        for key, dims in _KEYWORDS.items():
            if key in t:
                for d in dims:
                    amp = 0.06 * scale
                    self.touch(d, amp)
                    added[d] = added.get(d, 0.0) + amp
        try:
            v = float(getattr(affect, "v", 0.0) if affect is not None else 0.0)
            a = float(getattr(affect, "a", 0.5) if affect is not None else 0.5)
        except Exception:
            v, a = 0.0, 0.5
        if v > 0.15:
            self.touch("joy", min(0.08, v * 0.10)); added["joy"] = added.get("joy", 0) + 0.02
        elif v < -0.15:
            self.touch("sadness", min(0.08, -v * 0.10)); added["sadness"] = added.get("sadness", 0) + 0.02
        if a > 0.7:
            self.touch("surprise", 0.03); added["surprise"] = added.get("surprise", 0) + 0.03
        inj = dict(injection or {})
        for src, dst, base_w in (("threat", "fear", 0.20), ("stress", "stress", 0.20),
                                  ("guilt", "sadness", 0.12), ("shame", "sadness", 0.12),
                                  ("connection_delta", "trust", 0.15)):
            try:
                x = float(inj.get(src) or 0.0)
            except Exception:
                x = 0.0
            if abs(x) > 0.01:
                amp = x * base_w * scale
                self.touch(dst, amp)
                added[dst] = added.get(dst, 0.0) + amp
        return added

    def step(self) -> dict:
        incoming = {d: 0.0 for d in DIMS}
        for src, edges in COUPLING.items():
            s = float(self.values.get(src, 0.0))
            if abs(s) < 1e-6:
                continue
            for dst, w in edges:
                incoming[dst] += s * float(w)
        for d in DIMS:
            self.values[d] = clip(self.decay * float(self.values.get(d, 0.0))
                                  + self.gain * incoming[d], -self.cap, self.cap)
        # waves spread spatially, overlap, and decay
        for w in self.waves:
            w["age"] += 1.0
            w["amp"] *= 0.90
        self.waves = [w for w in self.waves[-32:] if abs(w["amp"]) > 0.002]
        for d in DIMS:
            pos = POS[d]
            spatial = 0.0
            for w in self.waves:
                dist = _dist(pos, w["pos"])
                spatial += w["amp"] * math.exp(-(dist * dist) / (2.0 * w["sigma"] ** 2))
            self.values[d] = clip(self.values[d] + 0.18 * spatial, -self.cap, self.cap)
        up = sum(abs(self.values[d]) for d in DIMS if POS[d][2] >= 0.5)
        lo = sum(abs(self.values[d]) for d in DIMS if POS[d][2] < 0.5)
        self.layer_upper = round(up, 4)
        self.layer_lower = round(lo, 4)
        self.layer_overlap = round(min(up, lo) * (1.0 + min(up, lo)), 4)
        self.last_step = sum(abs(v) for v in self.values.values())
        return dict(self.values)

    def apply_to(self, mind) -> None:
        try:
            affect = mind.affect
            v = 0.04 * (self.values["joy"] - self.values["sadness"])
            stress = 0.03 * self.values["fear"] + 0.02 * self.values["anger"] - 0.01 * self.values["trust"]
            affect.v = clip(float(affect.v) + v, -1.0, 1.0)
            affect.stress = clip(float(affect.stress) + stress, 0.0, 1.0)
            affect.a = clip(float(affect.a) + 0.02 * abs(self.values["surprise"]), 0.0, 1.0)
            affect.stress = clip(float(affect.stress) + 0.008 * float(getattr(self, "layer_overlap", 0.0)), 0.0, 1.0)
            affect.v = clip(float(affect.v) + 0.004 * (float(getattr(self, "layer_upper", 0.0)) - float(getattr(self, "layer_lower", 0.0))), -1.0, 1.0)
        except Exception:
            pass


    def outputs(self) -> dict:
        v = self.values
        memory_bias = 0.45 * (v["sadness"] + v["loneliness"]) - 0.35 * v["trust"] + 0.20 * v["joy"] - 0.15 * v["stress"]
        engage_bias = 0.35 * (v["joy"] + v["trust"]) - 0.35 * (v["stress"] + v["fear"])
        question_bias = 0.30 * v["trust"] + 0.20 * v["joy"] - 0.25 * v["sadness"]
        return {"memory_bias": round(clip(memory_bias, -1, 1), 3),
                "engage_bias": round(clip(engage_bias, -1, 1), 3),
                "question_bias": round(clip(question_bias, -1, 1), 3),
                "layer_upper": getattr(self, "layer_upper", 0.0),
                "layer_lower": getattr(self, "layer_lower", 0.0),
                "layer_overlap": getattr(self, "layer_overlap", 0.0)}

    def context(self) -> str:
        active = sorted(((d, round(float(v), 3)) for d, v in self.values.items() if abs(v) > 0.01),
                        key=lambda x: -abs(x[1]))[:5]
        if not active:
            return "平静"
        return ("scale=%.2f up=%.2f low=%.2f overlap=%.2f " % (
            float(self.last_scale), float(getattr(self, "layer_upper", 0.0)),
            float(getattr(self, "layer_lower", 0.0)), float(getattr(self, "layer_overlap", 0.0)))
            + " ".join("%s=%.2f" % (d, v) for d, v in active))
