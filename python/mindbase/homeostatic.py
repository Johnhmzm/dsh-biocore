"""Homeostatic controller: internal state decides whether to act.

Used for group participation; 1:1 proactive keeps its learned policy but
can read the same readiness signal.
"""
from __future__ import annotations

import math
import time
from typing import Dict


class HomeostaticController:
    def __init__(self, threshold: float = 0.78, cooldown_s: float = 120.0):
        self.threshold = float(threshold)
        self.cooldown_s = float(cooldown_s)

    def _num(self, x, default=0.5):
        try:
            return max(0.0, min(1.0, float(x)))
        except Exception:
            return default

    def readiness(self, mind, activity: float = 0.0, recency: float = 0.0,
                  trust: float = 0.5, now: float = 0.0,
                  last_action: float = 0.0,
                  alertness: float = 0.5,
                  allostatic_load: float = 0.0) -> Dict[str, object]:
        now = float(now or time.time())
        drives = getattr(getattr(mind, "drives", None), "values", {}) or {}
        social = self._num(drives.get("social", 0.5))
        curiosity = self._num(drives.get("curiosity", 0.5))
        energy = self._num(drives.get("energy", 0.5))
        safety = self._num(drives.get("safety", 0.5))
        aff = getattr(mind, "affect", None)
        stress = self._num(getattr(aff, "stress", 0.2), 0.2)
        try:
            load = self._num(mind.drives.allostatic_load(), 0.2)
        except Exception:
            load = 0.2
        needs = {}
        try:
            needs = dict(getattr(getattr(mind, "needs", None), "deficits", {}) or {})
        except Exception:
            needs = {}
        need_pressure = max(needs.values()) if needs else 0.0
        # low energy and high stress suppress action; social/curiosity pull toward it
        alert = self._num(alertness, 0.5)
        ext_load = self._num(allostatic_load, 0.0)
        load = max(load, ext_load)
        inhibit = self._num(0.55 * stress + 0.35 * load + 0.25 * (1.0 - energy)
                            + 0.08 * (1.0 - alert))
        raw = (0.28 * social + 0.18 * curiosity + 0.14 * energy + 0.10 * safety +
               0.12 * (1.0 - stress) + 0.10 * (1.0 - load) +
               0.06 * (alert - 0.5) +
               0.10 * self._num(activity) + 0.08 * self._num(recency) +
               0.06 * self._num(trust) + 0.04 * self._num(need_pressure))
        score = max(0.0, min(1.0, raw - 0.15 * inhibit))
        cooling = (now - float(last_action or 0.0)) < self.cooldown_s
        return {"readiness": round(score, 4), "threshold": self.threshold,
                "speak": bool(score >= self.threshold and not cooling),
                "cooling": cooling, "social": round(social, 3),
                "curiosity": round(curiosity, 3), "energy": round(energy, 3),
                "stress": round(stress, 3), "allostatic_load": round(load, 3),
                "need_pressure": round(need_pressure, 3),
                "activity": round(self._num(activity), 3),
                "recency": round(self._num(recency), 3),
                "trust": round(self._num(trust), 3),
                "inhibit": round(inhibit, 3),
                "alertness": round(alert, 3),
                "external_allostatic": round(ext_load, 3)}
