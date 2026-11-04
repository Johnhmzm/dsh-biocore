from __future__ import annotations

from collections import deque
import random
from typing import Deque, Dict, List, Optional

from .insight import InsightEngine
from .fly_tasks import associative_score
from .noise import DEFAULT_NOISE_GENES, NOISE_GENE_BOUNDS, EndogenousNoise


class CriticalityExperiment:
    name = "criticality"

    def __init__(self, mind):
        self.mind = mind
        self.history: Deque[Dict[str, float]] = deque(maxlen=200)

    def on_state(self, state) -> None:
        return None

    def on_action(self, action) -> None:
        return None

    def on_outcome(self, outcome) -> None:
        try:
            c = getattr(self.mind, "critical_state", {}) or {}
            self.history.append({
                "t": float(getattr(self.mind, "t", 0.0)),
                "metastability": float(c.get("metastability", 0.0)) if isinstance(c, dict) else 0.0,
                "viability": float(outcome.viability),
            })
        except Exception:
            pass

    def summary(self) -> Dict[str, object]:
        if not self.history:
            return {"n": 0}
        ms = [x["metastability"] for x in self.history]
        return {"n": len(ms), "metastability_mean": round(sum(ms) / len(ms), 4), "metastability_last": ms[-1]}


class InsightExperiment:
    name = "insight"

    def __init__(self, mind):
        self.mind = mind
        self.engine = InsightEngine(mind.memory)
        self.last: Optional[Dict[str, object]] = None
        self.count = 0

    def on_state(self, state) -> None:
        return None

    def on_action(self, action) -> None:
        return None

    def on_outcome(self, outcome) -> None:
        return None

    def on_turn(self, text: str, reply: str) -> None:
        try:
            c = getattr(self.mind, "critical_state", {}) or {}
            crit = float(c.get("metastability", 0.0)) if isinstance(c, dict) else 0.0
            row = self.engine.generate(str(text or ""), float(getattr(self.mind, "t", 0.0)), k=6, criticality=crit)
            if row:
                row["reply"] = str(reply or "")
                self.last = row
                self.count += 1
        except Exception:
            pass

    def summary(self) -> Dict[str, object]:
        return {"count": self.count, "last": self.last}


BASE_BOUNDS = [
    (0.01, 0.12), (-0.10, 0.10), (0.02, 0.12), (0.1, 1.2), (0.80, 0.99),
    (0.8, 1.3), (0.4, 1.2), (0.3, 1.1), (0.3, 0.8), (0.3, 0.8), (0.0, 0.002),
]
BASE_GENOME_LEN = len(BASE_BOUNDS)

def apply_genome(mind, genome, base_gain: float = 1.0, create_fly: bool = True, noise_system=None) -> None:
    """Apply one 11-gene (or 17-gene with noise) genome to a MindBase instance."""
    lr, gain, sparsity, apl, trace, gamma, alpha, beta, pam_ratio, ppl1_ratio, decay = tuple(genome[:11])
    if noise_system is not None and len(genome) > BASE_GENOME_LEN:
        noise_system.configure(tuple(genome[BASE_GENOME_LEN:BASE_GENOME_LEN + 6]))
        try:
            mind.endogenous_noise = noise_system
        except Exception:
            pass
    mind.mb.lr = float(lr)
    if hasattr(mind, "autonomy"):
        mind.autonomy.gain = float(max(0.0, min(1.5, float(base_gain) + float(gain))))
    circuit = getattr(mind, "circuit", None)
    if circuit is None:
        return
    circuit.sparsity = float(sparsity)
    fly = getattr(circuit, "fly_mb", None)
    if fly is None and create_fly:
        # The fly circuit is lazily created on first use; offline evaluation
        # creates it now so the genes affect the actual substrate.  Online
        # sessions keep the lazy path to avoid allocation at session creation.
        try:
            from .fly_mb import FlyMushroomBody
            fly = FlyMushroomBody(seed=1, sparsity=float(sparsity))
            circuit.fly_mb = fly
        except Exception:
            fly = None
    if fly is not None:
        fly.sparsity = float(sparsity)
        fly.apl_gain = float(apl)
        fly.trace_decay = float(trace)
        fly.subregion_gain = {"gamma": float(gamma), "alpha": float(alpha), "beta": float(beta)}
        fly.pam_gamma_ratio = float(pam_ratio)
        fly.ppl1_alpha_ratio = float(ppl1_ratio)
        fly.weight_decay = float(decay)

class EvolutionExperiment:
    name = "evolution"

    def __init__(self, mind, fly_weight: float = 0.1, novelty_weight: float = 0.01,
                 noise: bool = False, noise_weight: float = 0.0):
        self.mind = mind
        self.fly_weight = float(fly_weight)
        self.novelty_weight = float(novelty_weight)
        self.noise = bool(noise)
        self.noise_weight = float(noise_weight)
        self.genome_len = BASE_GENOME_LEN + (6 if self.noise else 0)
        self.bounds = list(BASE_BOUNDS) + ([tuple(x) for x in NOISE_GENE_BOUNDS] if self.noise else [])
        self.noise_system = EndogenousNoise(seed=1) if self.noise else None
        self.base_gain = float(getattr(getattr(mind, "autonomy", None), "gain", 1.0))
        self.population = [
            (0.04, -0.05, 0.03, 0.40, 0.95, 1.20, 0.80, 0.70, 0.60, 0.50, 0.0002),
            (0.06, -0.02, 0.05, 0.60, 0.90, 1.05, 0.75, 0.65, 0.55, 0.45, 0.0001),
            (0.07, 0.0, 0.07, 0.60, 0.95, 0.95, 0.85, 0.75, 0.65, 0.55, 0.0003),
            (0.08, 0.02, 0.05, 0.80, 0.80, 1.10, 0.70, 0.60, 0.50, 0.40, 0.0001),
            (0.10, 0.05, 0.06, 0.50, 0.92, 0.90, 0.80, 0.80, 0.60, 0.50, 0.0002),
            (0.05, 0.0, 0.08, 0.70, 0.88, 1.00, 0.75, 0.70, 0.58, 0.48, 0.0003),
        ]
        if self.noise:
            rng = random.Random(20261020)
            mixed = []
            for i, g in enumerate(self.population):
                if i == 0:
                    tail = tuple(DEFAULT_NOISE_GENES)
                else:
                    tail = tuple(round(rng.uniform(lo, hi), 4) for lo, hi in NOISE_GENE_BOUNDS)
                mixed.append(tuple(g) + tuple(tail))
            self.population = mixed
        self.scores = [[] for _ in self.population]
        self.idx = 0
        self.n = 0
        self.generation = 0
        self.eval_turns = 5
        self.archive = {}
        self.archive_score = {}
        self.archive_limit = 200
        self.history = []
        self.child_params = []
        self.memory_F = [0.5, 0.5, 0.5, 0.5, 0.5]
        self.memory_CR = [0.9, 0.9, 0.9, 0.9, 0.9]
        self.memory_idx = 0
        self.success_F = []
        self.success_CR = []
        self._apply(0)

    def on_state(self, state) -> None:
        return None

    def on_action(self, action) -> None:
        return None

    def on_outcome(self, outcome) -> None:
        try:
            self.scores[self.idx].append(float(outcome.reward))
            self.n += 1
            if len(self.scores[self.idx]) >= self.eval_turns:
                if self.idx + 1 < len(self.population):
                    self.idx += 1
                    self._apply(self.idx)
                elif all(len(x) >= self.eval_turns for x in self.scores):
                    self._evolve()
        except Exception:
            pass

    def _fly_fitness(self, genome) -> float:
        try:
            genome = tuple(genome[:BASE_GENOME_LEN])
            lr, gain, sparsity, apl, trace, gamma, alpha, beta, pam_ratio, ppl1_ratio, decay = genome
            vals = [associative_score(sparsity=sparsity, pam_lr=lr, ppl1_lr=lr * 1.2, apl_gain=apl, trace_decay=trace,
                                      gamma=gamma, alpha=alpha, beta=beta,
                                      pam_gamma_ratio=pam_ratio, ppl1_alpha_ratio=ppl1_ratio, weight_decay=decay, seed=seed)
                    for seed in (1, 2, 3)]
            return round(sum(vals) / len(vals), 4)
        except Exception:
            return 0.0

    def _noise_fitness(self, genome) -> float:
        if not self.noise or len(genome) <= BASE_GENOME_LEN:
            return 0.0
        try:
            ns = EndogenousNoise(seed=1)
            ns.configure(tuple(genome[BASE_GENOME_LEN:BASE_GENOME_LEN + 6]))
            return float(ns.spontaneous_metrics(96).get("score", 0.0))
        except Exception:
            return 0.0

    def _novelty(self, genome) -> float:
        try:
            if not self.archive:
                return 0.0
            vals = [sum((float(a) - float(b)) ** 2 for a, b in zip(genome, other)) ** 0.5 for other in self.archive.values()]
            return min(vals) if vals else 0.0
        except Exception:
            return 0.0

    def _evolve(self) -> None:
        chat = [sum(x) / max(1, len(x)) for x in self.scores]
        fly = [self._fly_fitness(g) for g in self.population]
        mean = sum(fly) / max(1, len(fly))
        sd = (sum((x - mean) ** 2 for x in fly) / max(1, len(fly))) ** 0.5
        fly_norm = [(x - mean) / sd if sd > 1e-9 else 0.0 for x in fly]
        noise = [self._noise_fitness(g) for g in self.population]
        nmean = sum(noise) / max(1, len(noise))
        nsd = (sum((x - nmean) ** 2 for x in noise) / max(1, len(noise))) ** 0.5
        noise_norm = [(x - nmean) / nsd if nsd > 1e-9 else 0.0 for x in noise]
        means = [c + self.fly_weight * f + self.novelty_weight * self._novelty(g) + self.noise_weight * nn
                 for c, f, g, nn in zip(chat, fly_norm, self.population, noise_norm)]
        order = sorted(range(len(self.population)), key=lambda i: means[i], reverse=True)
        if self.child_params and len(self.child_params) == len(self.population):
            f_ok, c_ok = self.child_params[order[0]]
            self.success_F.append(float(f_ok))
            self.success_CR.append(float(c_ok))
            if self.success_F:
                self.memory_F[self.memory_idx] = round(sum(self.success_F) / len(self.success_F), 4)
                self.memory_CR[self.memory_idx] = round(sum(self.success_CR) / len(self.success_CR), 4)
                self.memory_idx = (self.memory_idx + 1) % len(self.memory_F)
                self.success_F = []
                self.success_CR = []
        parent_a = self.population[order[0]]
        parent_b = self.population[order[1]]
        best = self.population[order[0]]
        fly = getattr(getattr(self.mind, "circuit", None), "fly_mb", None)
        if fly is not None:
            kc = round(float(getattr(fly, "last_sparse", 0.0)), 2)
            mbon = round(max(0.0, float(getattr(fly, "mbon_approach", 0.0)) - float(getattr(fly, "mbon_avoid", 0.0))), 2)
            trace = round(float(__import__("numpy").linalg.norm(fly.trace)), 1)
            key = "kc_%.2f|mbon_%.2f|tr_%.1f" % (kc, mbon, trace)
        else:
            key = "sparsity_%.2f" % best[2]
        best_score = float(means[order[0]])
        if key not in self.archive and len(self.archive) >= self.archive_limit:
            worst = min(self.archive_score, key=lambda k: self.archive_score[k])
            self.archive.pop(worst, None)
            self.archive_score.pop(worst, None)
        if key not in self.archive or best_score > float(self.archive_score.get(key, -1e9)):
            self.archive[key] = best
            self.archive_score[key] = best_score
        children = [parent_a, parent_b]
        params = [(sum(self.memory_F) / len(self.memory_F), sum(self.memory_CR) / len(self.memory_CR)),
                  (sum(self.memory_F) / len(self.memory_F), sum(self.memory_CR) / len(self.memory_CR))]
        if self.archive:
            import random as _r
            elite = self.archive[_r.choice(list(self.archive))]
            if elite not in children:
                children.append(elite)
                params.append((sum(self.memory_F) / len(self.memory_F), sum(self.memory_CR) / len(self.memory_CR)))
        import random
        bounds = self.bounds
        while len(children) < len(self.population):
            a, b, c = (self.population[i] for i in random.sample(range(len(self.population)), 3))
            child = []
            j_rand = random.randrange(len(bounds))
            mi = random.randrange(len(self.memory_F))
            F = max(0.2, min(1.0, self.memory_F[mi] + random.uniform(-0.05, 0.05)))
            CR = max(0.5, min(0.99, self.memory_CR[mi] + random.uniform(-0.05, 0.05)))
            self.last_F = F
            self.last_CR = CR
            params_child = (F, CR)
            for j, (lo, hi) in enumerate(bounds):
                mutant = float(a[j]) + F * (float(b[j]) - float(c[j]))
                value = mutant if (random.random() < CR or j == j_rand) else float(a[j])
                child.append(round(max(lo, min(hi, value)), 6))
            if tuple(child) not in children:
                children.append(tuple(child))
                params.append(params_child)
        self.population = children
        self.child_params = params[:len(children)]
        self.scores = [[] for _ in self.population]
        self.idx = 0
        self.generation += 1
        self.history.append({"generation": self.generation, "means": [round(x, 4) for x in means],
                             "noise": [round(x, 4) for x in noise], "archive": list(self.archive.keys())})
        self.history = self.history[-50:]
        self._apply(0)

    def _apply(self, idx: int) -> None:
        apply_genome(self.mind, self.population[idx], self.base_gain, create_fly=False, noise_system=self.noise_system)

    def state(self) -> Dict[str, object]:
        return {"population": [list(x) for x in self.population], "scores": [list(x) for x in self.scores], "idx": self.idx, "n": self.n, "generation": self.generation, "archive": {k: list(v) for k, v in self.archive.items()}, "archive_score": dict(self.archive_score), "history": list(self.history), "child_params": [list(x) for x in self.child_params], "memory_F": list(self.memory_F), "memory_CR": list(self.memory_CR), "memory_idx": self.memory_idx}

    def load_state(self, data: Dict[str, object]) -> None:
        try:
            pop = data.get("population") or []
            if pop:
                self.population = [tuple(x) for x in pop]
                self.scores = [list(x) for x in (data.get("scores") or [[] for _ in pop])]
                self.idx = int(data.get("idx", 0))
                self.n = int(data.get("n", 0))
                self.generation = int(data.get("generation", 0))
                self.archive = {str(k): tuple(v) for k, v in (data.get("archive") or {}).items()}
                self.archive_score = {str(k): float(v) for k, v in (data.get("archive_score") or {}).items()}
                self.history = list(data.get("history") or [])
                self.child_params = [tuple(x) for x in (data.get("child_params") or [])]
                self.memory_F = [float(x) for x in (data.get("memory_F") or self.memory_F)]
                self.memory_CR = [float(x) for x in (data.get("memory_CR") or self.memory_CR)]
                self.memory_idx = int(data.get("memory_idx", 0))
                self._apply(min(self.idx, len(self.population) - 1))
        except Exception:
            pass

    def _trend(self) -> float:
        try:
            vals = [sum(h.get("means") or [0.0]) / max(1, len(h.get("means") or [0.0])) for h in self.history]
            if len(vals) < 2:
                return 0.0
            return round(vals[-1] - vals[0], 4)
        except Exception:
            return 0.0

    def summary(self) -> Dict[str, object]:
        return {
            "n": self.n,
            "trend": self._trend(),
            "generation": self.generation,
            "archive": list(self.archive.keys()),
            "archive_score": {k: round(float(v), 4) for k, v in self.archive_score.items()},
            "archive_limit": self.archive_limit,
            "history": list(self.history[-10:]),
            "novelty": [round(self._novelty(g), 4) for g in self.population],
            "F": round(sum(self.memory_F) / len(self.memory_F), 4),
            "CR": round(sum(self.memory_CR) / len(self.memory_CR), 4),
            "memory_F": [round(x, 4) for x in self.memory_F],
            "memory_CR": [round(x, 4) for x in self.memory_CR],
            "fly_fitness": [round(self._fly_fitness(g), 4) for g in self.population],
            "fly_weight": self.fly_weight,
            "novelty_weight": self.novelty_weight,
            "population": [[round(x, 4) for x in g] for g in self.population],
            "options": [[round(x, 4) for x in g] for g in self.population],
            "genome_len": self.genome_len,
            "noise": self.noise,
            "noise_weight": self.noise_weight,
            "noise_fitness": [round(self._noise_fitness(g), 4) for g in self.population],
            "lr": round(float(getattr(self.mind.mb, "lr", 0.0)), 4),
            "autonomy_gain": round(float(getattr(getattr(self.mind, "autonomy", None), "gain", 0.0)), 4),
        }
