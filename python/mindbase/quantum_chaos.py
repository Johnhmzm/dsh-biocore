"""Chaos + quantum-inspired uncertainty for the core.

This is NOT physical quantum mechanics. It is:
  - a deterministic chaotic field (Lorenz) supplying bounded noise
  - a Born-rule measurement over action amplitudes (quantum-inspired)
  - OU uncertainty channels with seeds

The goal is non-repeating, path-dependent internal variation, not a physics claim.
"""
from __future__ import annotations

import math
import random


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


class ChaosField:
    def __init__(self, seed: int = 2026):
        self.rng = random.Random(seed)
        self.x = 0.1 + 0.01 * self.rng.random()
        self.y = 0.0
        self.z = 20.0
        self.last = 0.5

    def step(self, dt: float = 0.005, steps: int = 20) -> dict:
        sigma, rho, beta = 10.0, 28.0, 8.0 / 3.0
        for _ in range(max(1, int(steps))):
            dx = sigma * (self.y - self.x)
            dy = self.x * (rho - self.z) - self.y
            dz = self.x * self.y - beta * self.z
            self.x += dx * float(dt)
            self.y += dy * float(dt)
            self.z += dz * float(dt)
        # bounded, signed-ish output from z and x
        self.last = _clip(0.5 + 0.25 * math.tanh(self.x / 20.0) + 0.25 * math.tanh((self.z - 25.0) / 20.0))
        return {"value": round(self.last, 4), "x": round(self.x, 3),
                "y": round(self.y, 3), "z": round(self.z, 3)}


class QuantumMeasurement:
    """Born-rule sampling over action amplitudes; collapse changes weights."""

    def __init__(self, seed: int = 2026):
        self.rng = random.Random(seed)
        self.last = {}

    def measure(self, amplitudes: dict) -> dict:
        items = [(str(k), max(0.0, float(v))) for k, v in dict(amplitudes or {}).items()]
        if not items:
            return {"action": "none", "probability": 0.0, "entropy": 0.0}
        total = sum(v for _, v in items) or 1.0
        probs = [(k, v / total) for k, v in items]
        r = self.rng.random()
        acc = 0.0
        chosen, p = probs[-1]
        for k, q in probs:
            acc += q
            if r <= acc:
                chosen, p = k, q
                break
        ent = -sum(q * math.log(q + 1e-9) for _, q in probs)
        self.last = {"action": chosen, "probability": round(p, 4),
                     "entropy": round(ent, 4), "roll": round(r, 4)}
        return dict(self.last)


class UncertaintyChannels:
    def __init__(self, seed: int = 2026):
        self.rng = random.Random(seed)
        self.values = {"affect": 0.0, "connection": 0.0, "neural": 0.0, "memory": 0.0}
        self.theta = 0.08
        self.sigma = 0.05

    def step(self, chaos: float = 0.5) -> dict:
        c = _clip(chaos)
        for k in list(self.values):
            self.values[k] += self.theta * (0.0 - self.values[k]) + self.sigma * (c - 0.5) + self.sigma * (self.rng.random() - 0.5)
            self.values[k] = max(-0.2, min(0.2, self.values[k]))
        return {k: round(v, 4) for k, v in self.values.items()}


class QuantumChaos:
    def __init__(self, seed: int = 2026):
        self.chaos = ChaosField(seed)
        self.quantum = QuantumMeasurement(seed + 1)
        self.uncertainty = UncertaintyChannels(seed + 2)

    def tick(self, amplitudes: dict | None = None) -> dict:
        c = self.chaos.step()
        q = self.quantum.measure(amplitudes or {"approach": 0.5, "avoid": 0.3, "repair": 0.2})
        u = self.uncertainty.step(c["value"])
        return {"chaos": c, "quantum": q, "uncertainty": u}
