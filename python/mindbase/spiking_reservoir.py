"""Small heterogeneous spiking liquid with key biological factors.

Not a whole-brain model. It keeps only factors that change dynamics:
heterogeneous neurons, E/I balance, sparse delayed synapses, refractoriness,
spike-frequency adaptation, STDP + homeostatic scaling, criticality control,
neuromodulation, oscillations, population/linear readout, and simple chaos
metrics (entropy / divergence proxy).
"""
from __future__ import annotations

import math
import random

from .noise import clip
from .liquid_readout import LiquidReadout


class SpikingReservoir:
    def __init__(self, n: int = 128, seed: int = 2026, input_dim: int = 8,
                 inhibitory_ratio: float = 0.22, out_degree: int = 6,
                 max_delay: int = 5):
        self.n = max(16, int(n))
        self.rng = random.Random(int(seed))
        self.input_dim = int(input_dim)
        self.inhibitory = [1 if self.rng.random() < inhibitory_ratio else 0 for _ in range(self.n)]
        # 0 regular, 1 fast, 2 slow/adapting
        self.ntype = [self.rng.choices((0, 1, 2), weights=(0.55, 0.25, 0.20))[0] for _ in range(self.n)]
        base_tau = {0: (8, 22), 1: (3, 9), 2: (20, 45)}
        self.tau = [self.rng.uniform(*base_tau[t]) for t in self.ntype]
        self.threshold = [self.rng.uniform(0.45, 0.85) for _ in range(self.n)]
        self.reset_v = [self.rng.uniform(0.0, 0.25) for _ in range(self.n)]
        self.v = [float(x) for x in self.reset_v]
        self.ref = [0] * self.n
        self.ref_steps = [self.rng.randint(1, 3) for _ in range(self.n)]
        # spike-frequency adaptation
        self.adapt = [0.0] * self.n
        self.adapt_inc = [0.12 if t == 2 else 0.0 for t in self.ntype]
        self.adapt_tau = [self.rng.uniform(20.0, 80.0) for _ in range(self.n)]
        self.last_spike = [0] * self.n
        # sparse delayed synapses
        self.max_delay = max(1, int(max_delay))
        self.syn = []
        for pre in range(self.n):
            for _ in range(max(1, int(out_degree))):
                post = self.rng.randrange(self.n)
                sign = -0.9 if self.inhibitory[pre] else 1.0
                w = sign * self.rng.uniform(0.04, 0.16)
                d = self.rng.randint(1, self.max_delay)
                self.syn.append([pre, post, w, d])
        self.input_w = [[self.rng.uniform(-0.85, 0.85) for _ in range(self.input_dim)]
                        for _ in range(self.n)]
        self.background = [self.rng.uniform(0.01, 0.12) for _ in range(self.n)]
        self.delay_buf = [[0] * self.n for _ in range(self.max_delay + 1)]
        self.state_vec = [0.0] * self.n
        self.readout = LiquidReadout(self.n, 4, lr=0.006, seed=seed + 9)
        self.last = {}
        self.spike_history = []
        self.rate_history = []
        # criticality + oscillations + modulation
        self.branching_prev = 0.0
        self.branching = 0.0
        self.criticality_gain = 1.0
        self.criticality_target = 1.0
        self.phase_theta = 0.0
        self.phase_gamma = 0.0
        self.da = 0.0
        self.ne = 0.0
        self.ht = 0.0
        self.entropy = 0.0
        self.divergence = 0.0
        self.stdp_updates = 0
        self.feedback = 0.0

    def set_modulation(self, da: float = 0.0, ne: float = 0.0, ht: float = 0.0) -> None:
        self.da, self.ne, self.ht = float(da), float(ne), float(ht)

    def step(self, inputs, dt: float = 5.0, substeps: int = 8,
             feedback: float = 0.0, modulation: dict | None = None) -> dict:
        if modulation:
            self.set_modulation(modulation.get("da", 0.0), modulation.get("ne", 0.0),
                                modulation.get("ht", 0.0))
        self.feedback = float(feedback)
        x = [float(v) for v in list(inputs or [])[:self.input_dim]]
        x += [0.0] * (self.input_dim - len(x))
        total_spikes = 0
        spikes_by_step = []
        self.phase_theta = (self.phase_theta + 2.0 * math.pi * dt / 6000.0) % (2.0 * math.pi)
        self.phase_gamma = (self.phase_gamma + 2.0 * math.pi * dt / 40.0) % (2.0 * math.pi)
        osc = 1.0 + 0.08 * math.sin(self.phase_theta) + 0.04 * math.sin(self.phase_gamma)
        mod_gain = 1.0 + 0.25 * self.da + 0.10 * self.ne - 0.12 * self.ht
        prev_spikes = [0] * self.n
        for _ in range(max(1, int(substeps))):
            delayed = self.delay_buf.pop(0)
            self.delay_buf.append([0] * self.n)
            cur = [0.0] * self.n
            for pre, post, w, d in self.syn:
                if d == 1:
                    cur[post] += w * (1.0 if delayed[pre] else 0.0)
                else:
                    self.delay_buf[d - 2][post] += w * (1.0 if delayed[pre] else 0.0)
            spikes = [0] * self.n
            for i in range(self.n):
                if self.ref[i] > 0:
                    self.ref[i] -= 1
                    self.v[i] = self.reset_v[i]
                    continue
                drive = sum(x[k] * self.input_w[i][k] for k in range(self.input_dim))
                drive = drive * osc * mod_gain + self.background[i] + cur[i]
                drive += 0.12 * self.feedback
                drive -= self.adapt[i]
                dv = (-self.v[i] + drive) * (float(dt) / max(1.0, self.tau[i]))
                self.v[i] = clip(self.v[i] + dv + self.rng.gauss(0.0, 0.006), 0.0, 2.0)
                self.adapt[i] = max(0.0, self.adapt[i] * (1.0 - dt / self.adapt_tau[i]))
                if self.v[i] >= self.threshold[i]:
                    spikes[i] = 1
                    self.ref[i] = self.ref_steps[i]
                    self.v[i] = self.reset_v[i]
                    self.adapt[i] = min(1.0, self.adapt[i] + self.adapt_inc[i])
            # STDP + homeostatic scaling (small, local)
            for s in self.syn:
                pre, post, w, d = s
                if spikes[pre] and not prev_spikes[pre]:
                    s[2] = clip(w - 0.001 * (1.0 + self.ht), -0.5, 0.5)
                    self.stdp_updates += 1
                if spikes[post] and not prev_spikes[post]:
                    s[2] = clip(w + 0.001 * (1.0 + self.da), -0.5, 0.5)
                    self.stdp_updates += 1
            self.delay_buf[0] = spikes
            prev_spikes = spikes
            total_spikes += sum(spikes)
            spikes_by_step.append(sum(spikes))
        # homeostatic scaling: keep mean absolute synapse near a target
        if self.stdp_updates % 64 == 0 and self.syn:
            mean_w = sum(abs(s[2]) for s in self.syn) / len(self.syn)
            scale = 1.0 + clip(0.10 - mean_w, -0.05, 0.05)
            for s in self.syn:
                s[2] = clip(s[2] * scale, -0.5, 0.5)
        n_steps = max(1, int(substeps))
        rate = total_spikes / float(self.n * n_steps)
        self.rate_history.append(rate)
        self.rate_history = self.rate_history[-64:]
        # branching / criticality
        if self.branching_prev > 0:
            self.branching = total_spikes / max(1.0, self.branching_prev)
        self.branching_prev = float(total_spikes)
        self.criticality_gain = clip(self.criticality_gain
                                     + 0.02 * (self.criticality_target - self.branching), 0.5, 1.6)
        # phase / entropy / divergence proxy
        rates = [s / n_steps for s in spikes_by_step] if spikes_by_step else [rate]
        mean_step = sum(rates) / max(1, len(rates))
        var = sum((r - mean_step) ** 2 for r in rates) / max(1, len(rates))
        probs = [max(0.0, r) for r in rates]
        z = sum(probs) or 1.0
        probs = [p / z for p in probs]
        self.entropy = -sum(p * math.log(p + 1e-9) for p in probs if p > 0)
        if len(self.rate_history) >= 2:
            self.divergence = abs(self.rate_history[-1] - self.rate_history[-2])
        active = [i for i in range(self.n) if self.v[i] > 0.25]
        ei = (sum(1 for i in active if self.inhibitory[i]) /
              max(1, sum(1 for i in active if not self.inhibitory[i])))
        signal = math.tanh((rate - 0.03) * 18.0)
        self.state_vec = [clip(self.v[i] / max(0.1, self.threshold[i]), 0.0, 1.0)
                          for i in range(self.n)]
        read = self.readout.predict(self.state_vec)
        self.last = {"rate": round(rate, 4), "variance": round(var, 5),
                     "ei_balance": round(ei, 3), "signal": round(signal, 4),
                     "active": len(active), "n": self.n,
                     "readout": [round(float(x), 3) for x in read],
                     "adapt_mean": round(sum(self.adapt) / self.n, 4),
                     "branching": round(self.branching, 3),
                     "criticality_gain": round(self.criticality_gain, 3),
                     "entropy": round(self.entropy, 4),
                     "divergence": round(self.divergence, 4),
                     "theta": round(self.phase_theta, 3),
                     "gamma": round(self.phase_gamma, 3),
                     "stdp_updates": self.stdp_updates}
        return dict(self.last)

    def liquid_state(self):
        return list(self.state_vec)

    def readout_targets(self, mind):
        try:
            v = float(getattr(mind.affect, "v", 0.0))
            st = float(getattr(mind.affect, "stress", 0.2))
            so = float(mind.drives.values.get("social", 0.5))
            unc = min(1.0, abs(float(self.last.get("variance") or 0.0)) * 8.0)
            return [v, st * 2.0 - 1.0, so * 2.0 - 1.0, unc]
        except Exception:
            return [0.0, -0.5, 0.0, 0.0]

    def learn_readout(self, mind) -> list:
        self.readout.learn(self.state_vec, self.readout_targets(mind))
        return list(self.readout.last)

    def context(self) -> str:
        if not self.last:
            return ""
        return ("rate=%.3f var=%.4f e/i=%.2f branch=%.2f ent=%.2f "
                "adapt=%.2f phase=%.1f") % (
            self.last.get("rate", 0), self.last.get("variance", 0),
            self.last.get("ei_balance", 0), self.last.get("branching", 0),
            self.last.get("entropy", 0), self.last.get("adapt_mean", 0),
            self.last.get("theta", 0))
