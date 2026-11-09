"""Parallel evaluation runner for independent genomes / seeds.

On Windows, ProcessPoolExecutor uses spawn: call run_parallel from a
__main__-guarded script (or the CLI below), not from library import.
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from typing import Dict, List, Sequence


def _worker(task: Dict[str, object]) -> Dict[str, object]:
    from .autonomy_evolve import AutonomyGenome, evaluate
    genome = AutonomyGenome(**dict(task.get("genome") or {}))
    env = dict(task.get("env") or {})
    r = evaluate(genome, **env)
    return {"id": task.get("id"), "fitness": r.get("fitness"),
            "lifespan": r.get("lifespan"), "dead": r.get("dead"),
            "needs": r.get("needs_satisfaction")}


def run_parallel(tasks: Sequence[Dict[str, object]], workers: int = 4) -> List[Dict[str, object]]:
    with cf.ProcessPoolExecutor(max_workers=max(1, int(workers))) as ex:
        return list(ex.map(_worker, list(tasks)))


def default_env(steps: int = 80) -> Dict[str, object]:
    return dict(steps=steps, mortality=True, starvation=0.03,
                damage_threshold=1.5, predator_risk=0.05,
                thirst_rate=0.010, inflammation_rate=0.004,
                pain_rate=0.002, regime_shift=20)


def make_tasks(n: int = 8, steps: int = 80) -> List[Dict[str, object]]:
    return [{"id": i, "genome": {}, "env": default_env(steps)} for i in range(max(1, int(n)))]


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tasks", type=int, default=8)
    ap.add_argument("--steps", type=int, default=80)
    args = ap.parse_args()
    tasks = make_tasks(args.tasks, args.steps)
    t0 = time.time()
    seq = [ _worker(t) for t in tasks ]
    seq_t = time.time() - t0
    t0 = time.time()
    par = run_parallel(tasks, args.workers)
    par_t = time.time() - t0
    print({"tasks": len(tasks), "workers": args.workers,
           "sequential_s": round(seq_t, 2), "parallel_s": round(par_t, 2),
           "speedup": round(seq_t / max(1e-9, par_t), 2),
           "same_best": max(x["fitness"] for x in seq) == max(x["fitness"] for x in par)})


if __name__ == "__main__":
    _main()
