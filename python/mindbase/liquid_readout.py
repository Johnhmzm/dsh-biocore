"""Small linear readout over the spiking liquid state."""
from __future__ import annotations

import random

from .noise import clip


class LiquidReadout:
    def __init__(self, n_in: int = 128, n_out: int = 4, lr: float = 0.01, seed: int = 2026):
        self.n_in = int(n_in)
        self.n_out = int(n_out)
        self.lr = float(lr)
        self.rng = random.Random(int(seed))
        self.W = [[self.rng.uniform(-0.08, 0.08) for _ in range(self.n_in)]
                  for _ in range(self.n_out)]
        self.b = [0.0] * self.n_out
        self.last = [0.0] * self.n_out
        self.updates = 0

    def predict(self, vec) -> list:
        x = [float(v) for v in list(vec or [])[:self.n_in]]
        x += [0.0] * (self.n_in - len(x))
        out = []
        for i in range(self.n_out):
            s = self.b[i] + sum(self.W[i][j] * x[j] for j in range(self.n_in))
            out.append(clip(s, -1.0, 1.0))
        self.last = out
        return list(out)

    def learn(self, vec, targets, lr=None) -> None:
        x = [float(v) for v in list(vec or [])[:self.n_in]]
        x += [0.0] * (self.n_in - len(x))
        lr = float(self.lr if lr is None else lr)
        y = list(targets or [])
        y += [0.0] * (self.n_out - len(y))
        for i in range(self.n_out):
            pred = self.b[i] + sum(self.W[i][j] * x[j] for j in range(self.n_in))
            err = clip(float(y[i]) - pred, -1.0, 1.0)
            self.b[i] = clip(self.b[i] + lr * err, -1.0, 1.0)
            row = self.W[i]
            for j in range(self.n_in):
                row[j] = clip(row[j] + lr * err * x[j], -0.5, 0.5)
        self.updates += 1

    def context(self) -> str:
        names = ("valence", "stress", "social", "uncertainty")
        return " ".join("%s=%.2f" % (names[i], self.last[i]) for i in range(min(4, len(self.last))))
