"""Search for a sufficiently intelligent low-level genome.

Intelligence is measured behaviourally, not by a single gene:
  - survival across environments
  - need satisfaction
  - lifespan / recovery
  - generalization to volatile and hard environments

The search mutates low-level physiology, emotion, need gates, planning and
quantum parameters; no chat data.
"""
from __future__ import annotations

import random
from typing import Dict, List, Sequence

from .autonomy_bench import run_autonomy
from .autonomy_evolve import AutonomyGenome, mutate
from .config import MindConfig
from .forage import ForagePolicy


def _policy(g: AutonomyGenome, seed: int) -> ForagePolicy:
    return ForagePolicy(
        forage_weight=g.forage_weight, rest_weight=g.rest_weight,
        social_weight=g.social_weight, temperature=g.forage_temperature,
        fear_weight=g.fear_weight, curiosity_weight=g.curiosity_weight,
        explore_weight=g.explore_weight, drink_weight=g.drink_weight,
        heal_weight=g.heal_weight, need_physio_gate=g.need_physio_gate,
        need_safety_gate=g.need_safety_gate, need_belong_gate=g.need_belong_gate,
        need_actual_gate=g.need_actual_gate, planning=g.planning,
        quantum_weight=g.quantum_weight, quantum_gamma=g.quantum_gamma,
        quantum_dephase=g.quantum_dephase, rng=random.Random(int(seed)),
    )


ENVIRONMENTS = {
    "static": dict(mortality=True, starvation=0.025, damage_threshold=2.0,
                   predator_risk=0.03, thirst_rate=0.008, inflammation_rate=0.003,
                   pain_rate=0.001),
    "volatile": dict(mortality=True, starvation=0.03, damage_threshold=1.5,
                     predator_risk=0.08, thirst_rate=0.012, inflammation_rate=0.004,
                     pain_rate=0.002, regime_shift=20),
    "hard": dict(mortality=True, starvation=0.04, damage_threshold=1.2,
                 predator_risk=0.12, thirst_rate=0.016, inflammation_rate=0.006,
                 pain_rate=0.003),
}


def evaluate_genome(g: AutonomyGenome, seeds: Sequence[int] = (1, 2),
                    steps: int = 80) -> Dict[str, object]:
    rows = {}
    for name, env in ENVIRONMENTS.items():
        surv = []
        needs = []
        lives = []
        for s in seeds:
            cfg = MindConfig(seed=int(s), autonomy_threshold=g.autonomy_threshold,
                             volition_temperature=g.volition_temperature,
                             autonomy_adapt_lr=g.autonomy_adapt_lr, noise_sigma=g.noise_sigma)
            policy = _policy(g, int(s))
            phys = {"hydration_decay": g.hydration_decay, "sleep_accum": g.sleep_accum,
                    "inflammation_gain": g.inflammation_gain, "cortisol_gain": g.cortisol_gain}
            emo = {"fear": {"stress": g.fear_stress}, "joy": {"vpos": g.joy_v}}
            r = run_autonomy(steps=steps, cfg=cfg, perturb=True, energy_policy=policy,
                             physiology_params=phys, emotion_weights=emo, **env)
            surv.append(0.0 if r.get("dead") else 1.0)
            needs.append(float((r.get("needs") or {}).get("satisfaction", 0.0)))
            lives.append(float(r.get("lifespan", steps)) / max(1.0, float(steps)))
        rows[name] = {"survival": sum(surv) / len(surv), "needs": sum(needs) / len(needs),
                      "life": sum(lives) / len(lives)}
    surv_all = sum(v["survival"] for v in rows.values()) / len(rows)
    needs_all = sum(v["needs"] for v in rows.values()) / len(rows)
    life_all = sum(v["life"] for v in rows.values()) / len(rows)
    generalization = min(v["survival"] for v in rows.values())
    score = 0.45 * surv_all + 0.35 * needs_all + 0.20 * life_all + 0.15 * generalization
    return {"score": round(score, 5), "survival": round(surv_all, 4),
            "needs": round(needs_all, 4), "life": round(life_all, 4),
            "generalization": round(generalization, 4), "environments": rows,
            "genome": g.__dict__}


def search(generations: int = 8, population: int = 8, steps: int = 80,
           seeds: Sequence[int] = (1, 2), seed: int = 1) -> Dict[str, object]:
    rng = random.Random(int(seed))
    pool: List[AutonomyGenome] = [AutonomyGenome() for _ in range(max(1, population))]
    # diversify the initial pool
    for i in range(1, len(pool)):
        pool[i] = mutate(pool[0], rng, scale=0.35)
    best_ever = None
    history = []
    for gen in range(max(1, generations)):
        results = [evaluate_genome(g, seeds=seeds, steps=steps) for g in pool]
        results.sort(key=lambda r: r["score"], reverse=True)
        best = results[0]
        if best_ever is None or best["score"] > best_ever["score"]:
            best_ever = best
        history.append({"generation": gen + 1, "best": best["score"],
                        "survival": best["survival"], "needs": best["needs"],
                        "generalization": best["generalization"]})
        elites = [AutonomyGenome(**r["genome"]) for r in results[:max(2, population // 3)]]
        pool = list(elites)
        while len(pool) < max(1, population):
            pool.append(mutate(rng.choice(elites), rng))
    final = evaluate_genome(pool[0], seeds=seeds, steps=steps)
    if best_ever is None or final["score"] > best_ever["score"]:
        best_ever = final
    return {"best": best_ever, "history": history}
