"""Generic uncertainty/chaos search.

This is deliberately not tied to the bot: any genome, objective, mutation,
crossover and behaviour descriptor can be plugged in.  A two-level density
matrix supplies an uncertainty signal that modulates mutation scale and
selection; a QD archive keeps diverse solutions.
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List, Tuple

from .quantum import QuantumUncertainty


class ChaosSearch:
    def __init__(self, objective: Callable[[Any], float],
                 mutate: Callable[..., Any],
                 descriptor: Callable[[Any], Tuple],
                 initials: List[Any],
                 rng: random.Random | None = None,
                 quantum_weight: float = 0.5,
                 quantum_gamma: float = 0.05,
                 quantum_dephase: float = 0.10,
                 mutation_scale: float = 0.20):
        self.objective = objective
        self.mutate = mutate
        self.descriptor = descriptor
        self.initials = list(initials)
        self.rng = rng or random.Random(1)
        self.quantum_weight = float(quantum_weight)
        self.quantum_gamma = float(quantum_gamma)
        self.quantum_dephase = float(quantum_dephase)
        self.mutation_scale = float(mutation_scale)
        self.q = QuantumUncertainty()
        self.archive: Dict[Tuple, Dict[str, Any]] = {}
        self.history: List[Dict[str, Any]] = []

    def _uncertainty_scale(self) -> float:
        self.q.step(1.0, gamma=self.quantum_gamma, dephase=self.quantum_dephase)
        return 1.0 + self.quantum_weight * self.q.entropy()

    def run(self, generations: int = 10, population: int = 8) -> Dict[str, Any]:
        # seed archive
        for g in self.initials:
            self._consider(g)
        for gen in range(max(1, int(generations))):
            scale = self._uncertainty_scale()
            elites = list(self.archive.values())
            candidates = []
            if elites:
                for e in elites[:max(1, population // 2)]:
                    candidates.append(self.mutate(e["genome"], self.rng,
                                                  self.mutation_scale * scale))
            while len(candidates) < max(1, population):
                base = self.rng.choice(elites)["genome"] if elites else self.rng.choice(self.initials)
                candidates.append(self.mutate(base, self.rng,
                                              self.mutation_scale * scale * 1.5))
            for g in candidates:
                self._consider(g)
            best = max(self.archive.values(), key=lambda e: e["score"])
            self.history.append({"generation": gen + 1, "archive": len(self.archive),
                                 "best": round(float(best["score"]), 5),
                                 "uncertainty_scale": round(scale, 4)})
            # feedback nudges the quantum state toward success
            self.q.nudge("speak", float(best["score"]))
        best = max(self.archive.values(), key=lambda e: e["score"])
        return {"best": best, "archive_size": len(self.archive),
                "history": self.history, "uncertainty": self.q.metrics()}

    def _consider(self, genome: Any) -> None:
        try:
            score = float(self.objective(genome))
        except Exception:
            return
        key = tuple(self.descriptor(genome))
        if key not in self.archive or score > self.archive[key]["score"]:
            self.archive[key] = {"genome": genome, "score": score, "descriptor": key}
