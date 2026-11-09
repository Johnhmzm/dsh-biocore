"""Small LIF neural sheet.

64 neurons, 20% inhibitory, fixed random connectivity. It is a local neural
detail layer, not a full brain: input current from affect/threat, output firing
rate back to the numeric brain/prompt.
"""
from __future__ import annotations

import numpy as np


class NeuralSheet:
    def __init__(self, n: int = 64, seed: int = 1):
        self.n = int(n)
        self.rng = np.random.default_rng(seed)
        self.v = self.rng.uniform(-65.0, -55.0, self.n)
        self.v_rest = -65.0
        self.v_reset = -70.0
        self.threshold = -50.0
        self.tau_m = 20.0
        self.dt = 2.0
        self.inhib = self.rng.random(self.n) < 0.2
        w = self.rng.normal(0.0, 1.0, (self.n, self.n)) / np.sqrt(self.n)
        w[self.inhib, :] *= -1.8
        np.fill_diagonal(w, 0.0)
        self.w = w
        self.last_rate = 0.0
        self.last_spikes = 0

    def step(self, current: float, steps: int = 25) -> float:
        spikes_total = 0
        for _ in range(max(1, int(steps))):
            synaptic = self.w @ (self.v > self.threshold).astype(float)
            dv = (-(self.v - self.v_rest) + 300.0 * float(current) + 2.0 * synaptic) * (self.dt / self.tau_m)
            self.v = self.v + dv
            spiked = self.v >= self.threshold
            count = int(spiked.sum())
            spikes_total += count
            self.v[spiked] = self.v_reset
        self.last_spikes = spikes_total
        self.last_rate = float(spikes_total) / (self.n * max(1, int(steps)))
        return round(min(1.0, self.last_rate * 20.0), 4)

    def context(self) -> str:
        return "局部神经元放电率=%.3f（%d 个脉冲）" % (self.last_rate, self.last_spikes)
