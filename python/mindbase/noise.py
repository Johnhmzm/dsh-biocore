from __future__ import annotations

import math
import random
from collections import deque
from typing import Deque, Dict, List, Sequence

from .criticality import AvalancheMeter, BranchingRatio, CriticalityController


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def ou(prev: float, mu: float, theta: float, sigma: float, dt: float, rng: random.Random) -> float:
    """Exact-ish Ornstein-Uhlenbeck step. theta = 1/tau."""
    if dt <= 0:
        return prev
    decay = math.exp(-theta * dt)
    # sigma is the stationary standard deviation (not white-noise amplitude)
    var = (sigma * sigma) * (1.0 - decay * decay)
    return prev * decay + mu * (1.0 - decay) + rng.gauss(0.0, math.sqrt(max(var, 1e-12)))


class MultiScaleNoise:
    """A cheap 1/f-ish generator: weighted sum of OU processes at multiple taus."""

    def __init__(self, taus: Sequence[float], rng: random.Random, weights: Sequence[float] | None = None):
        self.taus = list(taus)
        self.rng = rng
        self.weights = list(weights) if weights else [1.0 / len(taus)] * len(taus)
        self.values: List[float] = [0.0] * len(self.taus)

    def step(self, dt: float, sigma: float = 1.0) -> float:
        total = 0.0
        for i, tau in enumerate(self.taus):
            theta = 1.0 / max(tau, 1e-6)
            self.values[i] = ou(self.values[i], 0.0, theta, sigma, dt, self.rng)
            total += self.weights[i] * self.values[i]
        return total

    def snapshot(self) -> List[float]:
        return list(self.values)


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


DEFAULT_NOISE_GENES = (0.18, 1.0, 1.0, 0.25, 0.7, 0.12)
NOISE_GENE_BOUNDS = (
    (0.0, 0.60),   # noise_gain
    (0.0, 2.00),   # inhibition_gain
    (0.80, 1.20),  # criticality_target
    (0.0, 1.00),   # chaos_gain
    (0.10, 1.50),  # temperature
    (0.0, 0.50),   # exploration_rate
)


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 8:
        return 0.0
    xs = [float(x) for x in a[-n:]]
    ys = [float(y) for y in b[-n:]]
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return 0.0
    return float(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy))


class CriticalNoiseNetwork:
    """Small recurrent threshold network with a homeostatic criticality controller.

    It is intentionally cheap: binary units, sparse fixed out-degree, no training.
    The controller adjusts a gate so the branching ratio stays near a target.
    """

    def __init__(self, n_units: int = 64, out_degree: int = 6, seed: int = 1):
        self.n = max(16, int(n_units))
        self.out_degree = max(1, int(out_degree))
        self.rng = random.Random(int(seed))
        self.out = []
        for _ in range(self.n):
            targets = [self.rng.randrange(self.n) for _ in range(self.out_degree)]
            self.out.append(targets)
        self.branching = BranchingRatio(window=256)
        self.avalanche = AvalancheMeter(capacity=2048)
        self.controller = CriticalityController(target=1.0, lr=0.02, gate=1.0)
        self.ou = MultiScaleNoise([60.0, 600.0, 3600.0], self.rng, weights=[0.5, 0.3, 0.2])
        self.active = [0] * self.n
        self.count_history: Deque[int] = deque(maxlen=512)
        self.noise_history: Deque[float] = deque(maxlen=512)
        self.noise_gain = 0.18
        self.inhibition_gain = 1.0
        self.criticality_target = 1.0
        self.chaos_gain = 0.25
        self.temperature = 0.7
        self.exploration_rate = 0.12
        self.recurrent_gain = 1.0
        self.last_noise = 0.0
        self.last_count = 0
        self.reset()

    def reset(self) -> None:
        self.active = [1 if self.rng.random() < 0.05 else 0 for _ in range(self.n)]
        self.branching.reset()
        self.avalanche.reset()
        self.count_history.clear()
        self.noise_history.clear()
        self.controller.gate = 0.80 + 0.30 * float(self.inhibition_gain)
        self.last_noise = 0.0
        self.last_count = sum(self.active)

    def configure(self, genes: Sequence[float]) -> None:
        vals = list(genes) + [0.0] * (6 - len(genes))
        self.noise_gain = clip(float(vals[0]), 0.0, 0.60)
        self.inhibition_gain = clip(float(vals[1]), 0.0, 2.00)
        self.criticality_target = clip(float(vals[2]), 0.80, 1.20)
        self.chaos_gain = clip(float(vals[3]), 0.0, 1.00)
        self.temperature = clip(float(vals[4]), 0.10, 1.50)
        self.exploration_rate = clip(float(vals[5]), 0.0, 0.50)
        self.recurrent_gain = 1.0 + 0.40 * self.chaos_gain
        self.controller.target = self.criticality_target
        self.reset()

    def step(self, dt: float = 60.0, external: float = 0.0) -> int:
        drive = [0.0] * self.n
        for i, on in enumerate(self.active):
            if on:
                for j in self.out[i]:
                    drive[j] += 1.0
        noise = self.ou.step(float(dt), sigma=0.5)
        self.last_noise = float(noise)
        g = float(self.noise_gain)
        gate = float(self.controller.gate)
        temp = 0.40 + 0.50 * float(self.temperature)
        p_spont = 0.004 + 0.03 * g
        ds = [0.0] * self.n
        for j in range(self.n):
            d = self.recurrent_gain * drive[j] + float(external)
            d += g * noise + g * 0.75 * self.rng.gauss(0.0, 1.0)
            ds[j] = d
            p = sigmoid((d - gate) / max(0.05, temp))
            on = 1 if (self.rng.random() < p or self.rng.random() < p_spont) else 0
            self.active[j] = on
        count = sum(self.active)
        self.last_count = int(count)
        self.count_history.append(int(count))
        self.noise_history.append(float(noise))
        sigma = self.branching.update(int(count))
        self.avalanche.update(int(count))
        self.controller.update(float(sigma))
        # Homeostatic activity target: track the gate that yields 12% activity.
        frac = float(count) / max(1, self.n)
        mean_d = sum(ds) / max(1, self.n)
        target_frac = 0.12
        target_gate = mean_d - temp * math.log(target_frac / (1.0 - target_frac))
        self.controller.gate = 0.60 * self.controller.gate + 0.40 * target_gate
        if len(self.branching.history) > 8:
            self.controller.gate += 0.02 * (float(sigma) - float(self.criticality_target))
        self.controller.gate = max(0.2, min(12.0, self.controller.gate))
        return int(count)

    def metrics(self) -> Dict[str, float]:
        counts = list(self.count_history)
        if not counts:
            return {"n1": 0.0, "n2": 0.0, "n3": 0.0, "n4": 0.0, "n5": 0.0,
                    "score": 0.0, "branching_ratio": 0.0, "avalanche_exponent": 0.0}
        mean = sum(counts) / len(counts)
        var = sum((x - mean) ** 2 for x in counts) / len(counts)
        std = math.sqrt(max(0.0, var))
        frac = mean / max(1.0, float(self.n))
        n1 = max(0.0, min(1.0, 4.0 * frac * (1.0 - frac)))
        cv = std / max(1e-6, mean)
        n2 = max(0.0, min(1.0, cv / (1.0 + cv)))
        n3 = 0.60 * self.branching.score(self.criticality_target) + 0.40 * self.avalanche.score()
        n4 = max(0.0, min(1.0, abs(_corr(counts, list(self.noise_history)))))
        bins = (0, 1, 2, 3, 5, 9, 17, 33)
        hist = [0] * (len(bins) - 1)
        for x in counts:
            k = 0
            for i in range(len(bins) - 1):
                if bins[i] <= x < bins[i + 1]:
                    k = i
                    break
            else:
                k = len(hist) - 1
            hist[k] += 1
        total = max(1, len(counts))
        ent = 0.0
        for h in hist:
            if h:
                p = h / total
                ent -= p * math.log(max(p, 1e-12))
        n5 = max(0.0, min(1.0, ent / math.log(max(2, len(hist)))))
        score = 0.20 * n1 + 0.20 * n2 + 0.25 * n3 + 0.15 * n4 + 0.20 * n5
        return {
            "n1": round(n1, 5),
            "n2": round(n2, 5),
            "n3": round(n3, 5),
            "n4": round(n4, 5),
            "n5": round(n5, 5),
            "score": round(max(0.0, min(1.0, score)), 5),
            "branching_ratio": round(float(self.branching.sigma), 5),
            "avalanche_exponent": round(float(self.avalanche.exponent()), 5),
            "activity_mean": round(mean, 4),
            "activity_fraction": round(frac, 4),
            "activity_max": int(max(counts)),
        }


class EndogenousNoise:
    """Endogenous noise source: critical network + 1/f OU; maps to drives/affect.

    genes = (noise_gain, inhibition_gain, criticality_target, chaos_gain,
             temperature, exploration_rate)
    """

    def __init__(self, seed: int = 1, n_units: int = 64):
        self.rng = random.Random(int(seed))
        self.network = CriticalNoiseNetwork(n_units=n_units, out_degree=6, seed=int(seed))
        self.genes = tuple(DEFAULT_NOISE_GENES)
        self.configure(self.genes)

    def configure(self, genes: Sequence[float]) -> None:
        vals = list(genes) + [0.0] * (6 - len(genes))
        self.genes = (
            clip(float(vals[0]), 0.0, 0.60),
            clip(float(vals[1]), 0.0, 2.00),
            clip(float(vals[2]), 0.80, 1.20),
            clip(float(vals[3]), 0.0, 1.00),
            clip(float(vals[4]), 0.10, 1.50),
            clip(float(vals[5]), 0.0, 0.50),
        )
        self.network.configure(self.genes)

    @property
    def noise_gain(self) -> float:
        return float(self.genes[0])

    @property
    def temperature(self) -> float:
        return float(self.genes[4])

    @property
    def exploration_rate(self) -> float:
        return float(self.genes[5])

    def step(self, dt: float = 60.0, external: float = 0.0) -> int:
        return self.network.step(dt=float(dt), external=float(external))

    def inject(self, mind) -> None:
        if mind is None:
            return
        g = self.noise_gain
        try:
            nv = float(self.network.last_noise) * g
            mind.affect.v = clip(float(mind.affect.v) + 0.04 * nv, -1.0, 1.0)
            mind.affect.a = clip(float(mind.affect.a) + 0.03 * abs(nv), 0.0, 1.0)
            mind.affect.stress = clip(float(mind.affect.stress) + 0.02 * abs(nv), 0.0, 1.0)
        except Exception:
            pass
        try:
            dv = 0.02 * g * float(self.network.last_noise)
            for k in ("energy", "social", "safety", "curiosity"):
                mind.drives.values[k] = clip(float(mind.drives.values.get(k, 0.5)) + dv * 0.5, 0.0, 1.0)
        except Exception:
            pass
        try:
            mind.last_noise_temperature = float(self.temperature)
            mind.last_noise_exploration = float(self.exploration_rate)
        except Exception:
            pass

    def metrics(self) -> Dict[str, float]:
        out = self.network.metrics()
        out.update({
            "noise_gain": round(float(self.noise_gain), 4),
            "inhibition_gain": round(float(self.network.inhibition_gain), 4),
            "criticality_target": round(float(self.network.criticality_target), 4),
            "chaos_gain": round(float(self.network.chaos_gain), 4),
            "temperature": round(float(self.temperature), 4),
            "exploration_rate": round(float(self.exploration_rate), 4),
        })
        return out

    def spontaneous_metrics(self, steps: int = 96, dt: float = 60.0) -> Dict[str, float]:
        self.network.reset()
        for _ in range(max(8, int(steps))):
            self.step(dt=dt, external=0.0)
        return self.metrics()

    def snapshot(self) -> Dict[str, object]:
        return {"genes": list(self.genes), "metrics": self.metrics()}
