"""Slow allostatic load: chronic stress accumulates and recovers."""
from __future__ import annotations

from dataclasses import dataclass


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


@dataclass
class AllostaticLoad:
    load: float = 0.0
    tau_recovery_s: float = 12 * 3600.0

    def update(self, dt_s: float, stress: float, fatigue: float,
               sleep_debt: float, sleep_quality: float = 0.5) -> float:
        dt = max(0.0, float(dt_s))
        if dt <= 0:
            return self.load
        drive = 0.5 * _clip(stress) + 0.3 * _clip(fatigue) + 0.2 * _clip(sleep_debt)
        load_up = 0.0025 * max(0.0, drive - 0.25) * (dt / 60.0)
        recover = (1.0 - _clip(sleep_quality)) * (dt / max(1.0, self.tau_recovery_s)) * 0.20
        self.load = _clip(self.load + load_up - recover)
        return round(self.load, 4)

    def stress_bias(self) -> float:
        return round(0.10 * self.load, 4)
