"""Numeric circadian rhythm (local wall clock)."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


@dataclass
class Circadian:
    night_start: float = 23.0
    wake_hour: float = 7.0

    def hour(self, now: float = 0.0) -> float:
        return (time.localtime(float(now or time.time())).tm_hour +
                time.localtime(float(now or time.time())).tm_min / 60.0)

    def is_awake(self, now: float = 0.0) -> bool:
        h = self.hour(now)
        if self.night_start >= self.wake_hour:
            return not (h >= self.night_start or h < self.wake_hour)
        return self.wake_hour <= h < self.night_start

    def alertness(self, now: float = 0.0) -> float:
        h = self.hour(now)
        # peak around 10:00 and 16:00, trough 03:00-05:00
        x = 2.0 * math.pi * (h - 10.0) / 24.0
        day = 0.5 + 0.5 * math.cos(x)
        if 3.0 <= h <= 5.0:
            day *= 0.35
        return round(_clip(day), 4)

    def sleep_bias(self, now: float = 0.0) -> float:
        return round(_clip(1.0 - self.alertness(now)), 4)
