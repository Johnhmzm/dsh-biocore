"""Parallel independent QD runs (seeds / environments).

Each task is a full run_qd; no shared archive.  Useful for seed sweeps and
multi-chaos evaluation.  Call from a __main__ guard or the CLI on Windows.
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from typing import Dict, List, Sequence


def _qd_worker(task: Dict[str, object]) -> Dict[str, object]:
    from .autonomy_evolve import run_qd
    kw = dict(task.get("kwargs") or {})
    out = run_qd(**kw)
    b = out.get("best") or {}
    return {"id": task.get("id"), "archive": out.get("archive_size"),
            "fitness": b.get("fitness"), "lifespan": b.get("lifespan"),
            "needs": b.get("needs_satisfaction"), "dead": b.get("dead")}


def run_parallel_qd(tasks: Sequence[Dict[str, object]], workers: int = 4) -> List[Dict[str, object]]:
    with cf.ProcessPoolExecutor(max_workers=max(1, int(workers))) as ex:
        return list(ex.map(_qd_worker, list(tasks)))


def make_qd_tasks(n: int = 6, generations: int = 2, steps: int = 80,
                  seed_base: int = 1) -> List[Dict[str, object]]:
    return [
        {"id": i, "kwargs": {"generations": generations, "population": 6,
                             "steps": steps, "seed": seed_base + i,
                             "mortality": True, "starvation": 0.03,
                             "damage_threshold": 1.5, "predator_risk": 0.05,
                             "thirst_rate": 0.010, "inflammation_rate": 0.004,
                             "pain_rate": 0.002, "regime_shift": 20}}
        for i in range(max(1, int(n)))
    ]


def _main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--generations", type=int, default=2)
    ap.add_argument("--steps", type=int, default=80)
    args = ap.parse_args()
    tasks = make_qd_tasks(args.runs, args.generations, args.steps)
    t0 = time.time()
    seq = [_qd_worker(t) for t in tasks]
    seq_t = time.time() - t0
    t0 = time.time()
    par = run_parallel_qd(tasks, args.workers)
    par_t = time.time() - t0
    print({"runs": len(tasks), "workers": args.workers,
           "sequential_s": round(seq_t, 2), "parallel_s": round(par_t, 2),
           "speedup": round(seq_t / max(1e-9, par_t), 2),
           "same": sorted(round(float(x["fitness"] or 0), 6) for x in seq)
                   == sorted(round(float(x["fitness"] or 0), 6) for x in par)})


if __name__ == "__main__":
    _main()
