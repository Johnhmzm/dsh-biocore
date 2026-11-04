from __future__ import annotations

from typing import Dict

from .noise import clip


class Viability:
    """Biological autonomy needs a boundary: variables the system must keep in range."""

    def __init__(self):
        self.bounds = {
            "energy": (0.08, 0.98),
            "social": (0.05, 1.0),
            "safety": (0.15, 1.0),
        }

    def update(self, drives) -> Dict[str, object]:
        inside, deficits = [], {}
        for k, (lo, hi) in self.bounds.items():
            v = float(drives.get(k))
            deficits[k] = round(max(0.0, lo - v) + max(0.0, v - hi), 4)
            inside.append(deficits[k] == 0.0)
        viability = sum(inside) / len(inside)
        self.deficits = deficits
        return {
            "viability": round(viability, 3),
            "deficits": deficits,
            "alarm": round(sum(deficits.values()), 4),
            "dominant": max(deficits, key=lambda k: deficits[k]) if deficits else "none",
        }

    def dominant_deficit(self) -> str:
        deficits = getattr(self, "deficits", {}) or {}
        return max(deficits, key=lambda k: deficits[k]) if deficits else "none"

    def intrinsic_reward(self) -> float:
        return -float(getattr(self, "alarm", 0.0))

    def snapshot(self, drives) -> Dict[str, object]:
        return self.update(drives)
