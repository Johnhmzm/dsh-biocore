"""Meta-evolution of the autonomy fitness weights.

The selection criterion is itself mutable.  Outer loop mutates fitness weights;
inner loop runs quality-diversity over behavior genomes and reports archive
size / viability / entropy.  This removes the hand-set fitness rule.
"""
from __future__ import annotations

import random
from typing import Dict, List

from .autonomy_bench import run_autonomy
from .autonomy_evolve import run_qd
from .config import MindConfig
from .diversity import js_divergence


def normalize(w: Dict[str, float]) -> Dict[str, float]:
    keys = ("viability", "recovery", "entropy", "spont", "penalty")
    vals = {k: max(1e-6, float(w.get(k, 0.1))) for k in keys}
    total = sum(vals.values())
    return {k: round(v / total, 5) for k, v in vals.items()}


def mutate(w: Dict[str, float], rng: random.Random, scale: float = 0.15) -> Dict[str, float]:
    return normalize({k: float(w.get(k, 0.2)) * (1.0 + rng.gauss(0, scale)) for k in ("viability", "recovery", "entropy", "spont", "penalty")})


def _dist(counts: Dict[str, int]) -> Dict[str, float]:
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()} if total else {}


def evaluate_weights(w: Dict[str, float], inner_generations: int = 2, population: int = 4,
                     steps: int = 120, seed: int = 1,
                     seeds: tuple = (1, 2, 3)) -> Dict[str, object]:
    out = run_qd(generations=inner_generations, population=population, steps=steps, seed=seed, weights=w)
    best = out.get("best") or {}
    descs = out.get("descriptors") or []
    jsd = 0.0
    genome = best.get("genome") if isinstance(best, dict) else None
    if genome:
        dists = []
        for s in seeds:
            cfg = MindConfig(seed=int(s), autonomy_threshold=float(genome.get("autonomy_threshold", 0.9)),
                             volition_temperature=float(genome.get("volition_temperature", 0.0)),
                             autonomy_adapt_lr=float(genome.get("autonomy_adapt_lr", 0.004)),
                             noise_sigma=float(genome.get("noise_sigma", 0.03)))
            r = run_autonomy(steps=steps, cfg=cfg, perturb=False)
            dists.append(_dist(r["actions"]))
        vals = []
        for i in range(len(dists)):
            for j in range(i + 1, len(dists)):
                vals.append(js_divergence(dists[i], dists[j]))
        jsd = sum(vals) / max(1, len(vals))
    score = (0.35 * min(1.0, float(out.get("archive_size", 0)) / 25.0)
             + 0.30 * float(best.get("viability", 0.0))
             + 0.15 * float(best.get("entropy", 0.0))
             + 0.15 * min(1.0, jsd * 10.0)
             + 0.05 * float(best.get("spontaneous", 0.0)))
    return {"weights": w, "score": round(score, 5), "archive_size": out.get("archive_size", 0),
            "best": best, "descriptors": descs, "jsd_mean": round(jsd, 5)}


def run_meta(generations: int = 4, candidates: int = 4, inner_generations: int = 2,
             population: int = 4, steps: int = 120, seed: int = 1,
             seeds: tuple = (1, 2, 3)) -> Dict[str, object]:
    rng = random.Random(int(seed))
    initial = [
        {"viability": 1.0, "recovery": 0.5, "entropy": 0.3, "spont": 0.2, "penalty": 0.5},
        {"viability": 0.5, "recovery": 0.5, "entropy": 1.0, "spont": 1.0, "penalty": 0.5},
        {"viability": 2.0, "recovery": 1.0, "entropy": 0.1, "spont": 0.1, "penalty": 1.0},
        {"viability": 1.0, "recovery": 2.0, "entropy": 0.2, "spont": 0.2, "penalty": 0.5},
    ]
    pool = [normalize(w) for w in initial[:max(1, candidates)]]
    history = []
    for gen in range(max(1, int(generations))):
        results = [evaluate_weights(w, inner_generations=inner_generations, population=population,
                                    steps=steps, seed=seed, seeds=seeds) for w in pool]
        results.sort(key=lambda x: x["score"], reverse=True)
        pool = [r["weights"] for r in results[:max(1, min(2, len(results)))]]
        history.append({"generation": gen + 1, "scores": [r["score"] for r in results],
                        "archive_sizes": [r["archive_size"] for r in results],
                        "jsd": [r.get("jsd_mean", 0.0) for r in results],
                        "weights": pool})
        while len(pool) < max(1, int(candidates)):
            pool.append(mutate(rng.choice(pool), rng))
    final = evaluate_weights(pool[0], inner_generations=inner_generations, population=population,
                             steps=steps, seed=seed, seeds=seeds)
    return {"best_weights": final["weights"], "best_score": final["score"],
            "best": final["best"], "history": history}
