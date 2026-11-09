"""Robustness search across chaos levels.

The environment itself is chaotic.  A genome that only wins in one chaos level
is not selected here; it must keep survival/needs across several levels.
"""
from __future__ import annotations

import random
from typing import Dict, List, Sequence

from .autonomy_evolve import AutonomyGenome, crossover, evaluate, mutate

CHAOS_LEVELS = (0.0, 0.5, 0.8)
ENV = dict(mortality=True, starvation=0.02, damage_threshold=2.0,
           predator_risk=0.03, thirst_rate=0.006, inflammation_rate=0.002,
           pain_rate=0.001, regime_shift=20)


def robust_score(g: AutonomyGenome, seeds: Sequence[int] = (1, 2),
                 steps: int = 60, chaos_levels: Sequence[float] = CHAOS_LEVELS) -> Dict[str, object]:
    rows = []
    for chaos in chaos_levels:
        for s in seeds:
            r = evaluate(g, steps=steps, seed=int(s), env_chaos=float(chaos), **ENV)
            rows.append({
                "chaos": float(chaos), "fitness": float(r.get("fitness", 0.0)),
                "survival": 0.0 if r.get("dead") else 1.0,
                "needs": float(r.get("needs_satisfaction") or 0.0),
                "lifespan": float(r.get("lifespan", steps)) / max(1.0, float(steps)),
            })
    fits = [x["fitness"] for x in rows]
    surv = [x["survival"] for x in rows]
    needs = [x["needs"] for x in rows]
    mean_fit = sum(fits) / len(fits)
    var = sum((x - mean_fit) ** 2 for x in fits) / len(fits)
    std = var ** 0.5
    score = mean_fit - 0.6 * std - 0.40 * (1.0 - min(surv)) + 0.15 * (sum(needs) / len(needs))
    return {"score": round(score, 5), "mean_fitness": round(mean_fit, 5),
            "std_fitness": round(std, 5), "min_survival": round(min(surv), 3),
            "mean_needs": round(sum(needs) / len(needs), 4),
            "rows": rows, "genome": g.__dict__}


def run_robust_qd(generations: int = 6, population: int = 6, steps: int = 60,
                  seeds: Sequence[int] = (1, 2), seed: int = 1) -> Dict[str, object]:
    rng = random.Random(int(seed))
    pop: List[AutonomyGenome] = [AutonomyGenome() for _ in range(max(2, population))]
    for i in range(1, len(pop)):
        pop[i] = mutate(pop[0], rng, scale=0.35)
    best_ever = None
    history = []
    for gen in range(max(1, generations)):
        results = [robust_score(g, seeds=seeds, steps=steps) for g in pop]
        results.sort(key=lambda r: r["score"], reverse=True)
        if best_ever is None or results[0]["score"] > best_ever["score"]:
            best_ever = results[0]
        history.append({"generation": gen + 1, "best_score": results[0]["score"],
                        "mean_fitness": results[0]["mean_fitness"],
                        "std_fitness": results[0]["std_fitness"],
                        "min_survival": results[0]["min_survival"]})
        elites = [AutonomyGenome(**r["genome"]) for r in results[:max(2, population // 2)]]
        pop = list(elites)
        while len(pop) < max(2, population):
            if len(elites) >= 2 and rng.random() < 0.5:
                pop.append(crossover(rng.choice(elites), rng.choice(elites), rng))
            else:
                pop.append(mutate(rng.choice(elites), rng, scale=0.20))
    return {"best": best_ever, "history": history}
