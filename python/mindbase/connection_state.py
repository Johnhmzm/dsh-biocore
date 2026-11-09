"""Continuous social connection state, inspired by Jiwen (积温).

Five axes are reduced to three here; valence/arousal are read from affect.
No LLM call; deterministic drift and thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


@dataclass
class ConnectionState:
    connection: float = 0.0
    pride: float = 0.35
    immersion: float = 0.0
    rate_mult: float = 1.0
    force_threshold: float = 0.50
    last_tick: float = 0.0
    last_user: float = 0.0

    def note_user(self, now: float) -> None:
        self.connection = _clip(self.connection * 0.55 - 0.05)
        self.pride = _clip(self.pride - 0.06, -1.0, 1.0)
        self.last_user = float(now)

    def tick(self, now: float, valence: float = 0.0, stress: float = 0.0) -> None:
        now = float(now)
        if self.last_tick <= 0:
            self.last_tick = now
        dt_min = min(180.0, max(0.0, (now - self.last_tick) / 60.0))
        if dt_min <= 0:
            return
        silence_min = max(0.0, (now - self.last_user) / 60.0) if self.last_user else dt_min
        rate = 0.0025 * max(0.1, min(3.0, float(self.rate_mult)))
        if silence_min > 30:
            rate *= 1.0 + min(2.0, (silence_min - 30.0) / 60.0)
        if valence < -0.2:
            rate *= 0.75
        if stress > 0.7:
            rate *= 0.8
        self.connection = _clip(self.connection + rate * dt_min)
        if silence_min > 10:
            self.pride = _clip(self.pride + 0.0008 * dt_min, -1.0, 1.0)
        else:
            self.pride = _clip(self.pride - 0.0008 * dt_min, -1.0, 1.0)
        self.immersion = _clip(self.immersion - 0.01 * dt_min)
        self.last_tick = now

    def trigger(self) -> str:
        c = self.connection
        force = max(0.25, min(0.85, float(self.force_threshold)))
        if c >= force:
            return "contact_force"
        if c >= 0.35:
            return "find_activity" if self.pride >= 0.5 else "contact"
        if c >= 0.20:
            return "observation"
        return "idle"

    def style_context(self) -> str:
        c = self.connection
        p = self.pride
        if c < 0.20:
            return ""
        if c >= 0.50:
            return "社交连接=%.2f 骄傲=%.2f 沉浸=%.2f；已经憋不住想找人，别再硬撑。" % (c, p, self.immersion)
        if p >= 0.5:
            return "社交连接=%.2f 骄傲=%.2f 沉浸=%.2f；想找但嘴硬，可以找点事做，别直接说想。" % (c, p, self.immersion)
        return "社交连接=%.2f 骄傲=%.2f 沉浸=%.2f；有点想说话，但不一定非要现在开口。" % (c, p, self.immersion)
