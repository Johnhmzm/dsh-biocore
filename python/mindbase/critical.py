from __future__ import annotations

import numpy as np


class CriticalDynamics:
    """Recurrent dynamics near the edge of chaos (spectral radius ~= 1)."""

    def __init__(self, seed: int = 1, n: int = 64):
        self.rng = np.random.default_rng(seed)
        self.n = n
        self.w = self.rng.normal(0, 1.0 / np.sqrt(n), (n, n))
        radius = max(1e-6, float(np.max(np.abs(np.linalg.eigvals(self.w)))))
        self.w *= 1.0 / radius
        self.x = self.rng.normal(0, 0.1, n)
        self.history = []

    def step(self, external: float = 0.0) -> dict:
        noise = self.rng.normal(0, 0.01, self.n)
        self.x = np.tanh(self.w @ self.x + 0.4 * float(external) + noise)
        self.history.append(float(np.mean(self.x)))
        self.history = self.history[-200:]
        self.metastability = float(np.std(self.history)) if len(self.history) > 5 else 0.0
        return {
            "mean": round(float(np.mean(self.x)), 4),
            "variance": round(float(np.var(self.x)), 4),
            "metastability": round(float(np.std(self.history)), 4) if len(self.history) > 5 else 0.0,
            "saturation": round(float(np.mean(np.abs(self.x) > 0.95)), 4),
        }

    def flexibility(self) -> float:
        # metastability normalized for downstream use
        return round(min(1.0, max(0.0, float(self.step.__self__.history and 0 or 0))), 4) if False else round(min(1.0, abs(float(getattr(self, "metastability", 0.0)))), 4)

    def spectral_radius(self) -> float:
        return round(float(np.max(np.abs(np.linalg.eigvals(self.w)))), 4)
