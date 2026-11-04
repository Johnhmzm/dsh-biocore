from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List

from . import MindBase, MindConfig
from .grid_world import EmbodiedLoop, GridWorld

OUT = Path(__file__).resolve().parent / "data" / "evolution.json"


def evaluate(params: Dict[str, float], seed: int = 1) -> float:
    mb = MindBase(MindConfig(seed=seed))
    mb.autonomy.allostatic_gain = float(params["allostatic_gain"])
    mb.autonomy.threshold = float(params["threshold"])
    mb.autonomy.tau = float(params["tau"])
    r = EmbodiedLoop(mb, GridWorld(seed=seed, noise=0.1), traits={"hunger_threshold": float(params.get("hunger_threshold", 0.5))}).run(150, 100)
    fitness = 0.45 * min(1.0, r["energy"]) + 0.35 * r["autonomy"]["initiative"] + 0.20 * r["viability"]
    return round(float(fitness), 4)


def evaluate_avg(params: Dict[str, float], seeds) -> float:
    vals = [evaluate(params, sd) for sd in seeds]
    return round(sum(vals) / max(1, len(vals)), 4)


def evolve(generations: int = 5, population: int = 4, seed: int = 1) -> Dict[str, object]:
    rng = random.Random(seed)
    base = {"allostatic_gain": 0.05, "threshold": 0.90, "tau": 900.0, "hunger_threshold": 0.50}
    best = dict(base)
    best_fit = evaluate(best, seed)
    archive = {}
    history: List[Dict[str, object]] = [{"gen": 0, "fitness": best_fit, "params": dict(best)}]
    for g in range(1, generations + 1):
        parents = [v["params"] for v in archive.values()] or [best]
        for _ in range(population):
            parent = rng.choice(parents)
            p = {
                "allostatic_gain": max(0.0, min(0.3, parent["allostatic_gain"] + rng.gauss(0, 0.01))),
                "threshold": max(0.5, min(1.5, parent["threshold"] + rng.gauss(0, 0.05))),
                "tau": max(300.0, min(1800.0, parent["tau"] + rng.gauss(0, 60))),
                "hunger_threshold": max(0.20, min(0.80, parent["hunger_threshold"] + rng.gauss(0, 0.05))),
            }
            fit = evaluate_avg(p, (seed + g * 10, seed + g * 10 + 1))
            slot = (round(p["hunger_threshold"], 1), round(p["threshold"], 1))
            if slot not in archive or fit > archive[slot]["fitness"]:
                archive[slot] = {"fitness": fit, "params": dict(p)}
            if fit > best_fit:
                best_fit, best = fit, dict(p)
        parents = [v["params"] for v in archive.values()]
        history.append({"gen": g, "fitness": best_fit, "archive": len(archive), "params": dict(best)})
    held = [11, 22, 33]
    base_gen = sum(evaluate(base, sd) for sd in held) / len(held)
    best_gen = sum(evaluate(best, sd) for sd in held) / len(held)
    result = {
        "base_fitness": history[0]["fitness"], "best_fitness": best_fit, "params": best,
        "generalization": {"base": round(base_gen, 4), "best": round(best_gen, 4)},
        "history": history,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def validate(best_params: Dict[str, float], base_params: Dict[str, float] | None = None, seeds=range(100, 120)) -> Dict[str, object]:
    base = base_params or {"allostatic_gain": 0.05, "threshold": 0.90, "tau": 900.0, "hunger_threshold": 0.50}
    bs = [evaluate(base, sd) for sd in seeds]
    es = [evaluate(best_params, sd) for sd in seeds]
    wins = sum(e > b for b, e in zip(bs, es))
    return {
        "base": round(sum(bs) / max(1, len(bs)), 4),
        "evolved": round(sum(es) / max(1, len(es)), 4),
        "wins": wins,
        "n": len(bs),
        "pass": wins > len(bs) / 2,
    }
