"""Birth-growth-death-reproduction lifecycle over low-level genomes.

QD archives keep solutions; this module adds an actual population lifecycle:
  - age increases every generation
  - low fitness / death -> removed
  - old individuals die
  - survivors reproduce via crossover + mutation
No chat data; selection is survival / needs.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Dict, List

from .autonomy_evolve import AutonomyGenome, crossover, evaluate, mutate


@dataclass
class Individual:
    genome: AutonomyGenome
    age: int = 0
    fitness: float = 0.0
    lifespan: int = 0
    dead: bool = False


def run_lifecycle(generations: int = 8, population: int = 10, steps: int = 60,
                  max_age: int = 6, seed: int = 1) -> Dict[str, object]:
    rng = random.Random(int(seed))
    pop: List[Individual] = [Individual(AutonomyGenome()) for _ in range(max(2, population))]
    for i in range(1, len(pop)):
        pop[i] = Individual(mutate(pop[0].genome, rng, scale=0.35))
    history = []
    for gen in range(max(1, int(generations))):
        births = 0
        deaths = 0
        # evaluate + age
        for ind in pop:
            r = evaluate(ind.genome, steps=steps, seed=int(seed) + gen,
                         mortality=True, starvation=0.02, damage_threshold=2.0,
                         predator_risk=0.03, thirst_rate=0.006,
                         inflammation_rate=0.002, pain_rate=0.001)
            ind.fitness = float(r.get("fitness", 0.0))
            ind.lifespan = int(r.get("lifespan", steps))
            ind.dead = bool(r.get("dead", False))
            ind.age += 1
        survivors = [ind for ind in pop if not ind.dead and ind.age < max_age]
        deaths = len(pop) - len(survivors)
        if not survivors:
            survivors = [max(pop, key=lambda x: x.fitness)]
        survivors.sort(key=lambda x: x.fitness, reverse=True)
        # reproduction from top parents
        need = max(2, int(population)) - len(survivors)
        while need > 0 and len(survivors) >= 1:
            if len(survivors) >= 2 and rng.random() < 0.6:
                a, b = rng.choice(survivors[:max(2, len(survivors) // 2)]).genome, rng.choice(survivors).genome
                child = Individual(crossover(a, b, rng), age=0)
            else:
                child = Individual(mutate(rng.choice(survivors).genome, rng, scale=0.15), age=0)
            survivors.append(child)
            births += 1
            need -= 1
        pop = survivors[:max(1, int(population))]
        best = max(pop, key=lambda x: x.fitness)
        history.append({
            "generation": gen + 1,
            "population": len(pop),
            "births": births,
            "deaths": deaths,
            "mean_fitness": round(sum(i.fitness for i in pop) / len(pop), 4),
            "best_fitness": round(float(best.fitness), 4),
            "best_planning": round(float(best.genome.planning), 3),
            "best_quantum": round(float(best.genome.quantum_weight), 3),
            "mean_age": round(sum(i.age for i in pop) / len(pop), 2),
        })
    best = max(pop, key=lambda x: x.fitness)
    return {"population": [{"genome": asdict(i.genome), "age": i.age,
                            "fitness": i.fitness, "lifespan": i.lifespan,
                            "dead": i.dead} for i in pop],
            "best": {"genome": asdict(best.genome), "fitness": best.fitness,
                     "age": best.age, "lifespan": best.lifespan},
            "history": history}
