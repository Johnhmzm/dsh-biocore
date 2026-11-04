"""Criticality meters and controllers for endogenous noise.

Operational proxies only: branching ratio, avalanche distribution, metastability.
These are *not* claims of consciousness; they measure structured spontaneous activity.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Deque, Dict, List, Optional


class BranchingRatio:
    """Estimate sigma = E[n_{t+1} / n_t] for active steps (n_t > 0)."""

    def __init__(self, window: int = 256):
        self.window = max(8, int(window))
        self.history: Deque[float] = deque(maxlen=self.window)
        self.prev = 0

    def reset(self) -> None:
        self.history.clear()
        self.prev = 0

    def update(self, activity: int) -> float:
        n = max(0, int(activity))
        if self.prev > 0:
            self.history.append(float(n) / float(self.prev))
        self.prev = n
        return self.sigma

    @property
    def sigma(self) -> float:
        if not self.history:
            return 0.0
        return float(sum(self.history) / len(self.history))

    def score(self, target: float = 1.0) -> float:
        if not self.history:
            return 0.0
        return max(0.0, 1.0 - min(1.0, abs(self.sigma - float(target))))


class AvalancheMeter:
    """Collect activity counts and fit a crude power-law slope on log bins."""

    def __init__(self, capacity: int = 2048):
        self.counts: Deque[int] = deque(maxlen=max(32, int(capacity)))

    def reset(self) -> None:
        self.counts.clear()

    def update(self, activity: int) -> None:
        self.counts.append(max(0, int(activity)))

    def exponent(self) -> float:
        vals = [x for x in self.counts if x > 0]
        if len(vals) < 16:
            return 0.0
        # coarse log bins
        bins: Dict[int, int] = {}
        for x in vals:
            b = int(math.floor(math.log2(max(1, x))))
            bins[b] = bins.get(b, 0) + 1
        xs: List[float] = []
        ys: List[float] = []
        for b, c in sorted(bins.items()):
            if c > 0:
                xs.append(math.log(max(1.0, float(2 ** b))))
                ys.append(math.log(float(c)))
        if len(xs) < 3:
            return 0.0
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 1e-9:
            return 0.0
        return float(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den)

    def score(self) -> float:
        # target slope ~ -1.5 (classic avalanche exponent)
        return max(0.0, 1.0 - min(1.0, abs(self.exponent() + 1.5) / 1.5))


class CriticalityController:
    """Homeostatic controller: adjust a gate toward a target branching ratio."""

    def __init__(self, target: float = 1.0, lr: float = 0.02, gate: float = 1.0):
        self.target = float(target)
        self.lr = float(lr)
        self.gate = float(gate)
        self.last_sigma = 0.0

    def update(self, sigma: float) -> float:
        self.last_sigma = float(sigma)
        if self.last_sigma > 0.0:
            self.gate += self.lr * (self.last_sigma - self.target)
            self.gate = max(0.2, min(3.0, self.gate))
        return self.gate


class MetastabilityMeter:
    """Cheap state-switching / dwell-time proxy."""

    def __init__(self, window: int = 256):
        self.values: Deque[float] = deque(maxlen=max(16, int(window)))

    def update(self, value: float) -> None:
        self.values.append(float(value))

    def autocorrelation(self, lag: int = 1) -> float:
        xs = list(self.values)
        if len(xs) <= lag + 2:
            return 0.0
        a = xs[:-lag]
        b = xs[lag:]
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        if va <= 1e-12 or vb <= 1e-12:
            return 0.0
        return float(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb))
