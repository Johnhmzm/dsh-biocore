"""Offline evolution loop: DSH/scripted teacher -> simulated env -> 11-gene DE/SHADE.

This is the no-human training loop:
  Teacher generates scenarios and scores outcomes.
  SimEnv runs MindBase on those scenarios.
  EvolutionExperiment (11 genes, DE/rand/1/bin + SHADE memory) evolves the base.
No LLM is needed for the base decisions; a DSH/LLM teacher is optional.

CLI:
  python -m mindbase.evolve_offline --generations 10 --scenarios 50 --seed 1
  python -m mindbase.evolve_offline --teacher dsh --generations 3 --scenarios 10
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import MindConfig
from .engine import MindBase
from .experiments import EvolutionExperiment, apply_genome
from .runtime import Kernel
from .sim_env import SimEnv
from .fly_tasks import associative_score
from .noise import EndogenousNoise
from .teacher import DSHTeacher, ScriptedTeacher, Teacher

DATA = Path(__file__).resolve().parent / "data" / "offline_evolution.json"


def fly_fitness(genome: Sequence[float]) -> float:
    """Learning capability of a genome on the associative fly task (3 seeds)."""
    try:
        genome = tuple(genome[:11])
        lr, gain, sparsity, apl, trace, gamma, alpha, beta, pam_ratio, ppl1_ratio, decay = genome
        vals = [associative_score(sparsity=sparsity, pam_lr=lr, ppl1_lr=lr * 1.2, apl_gain=apl,
                                  trace_decay=trace, gamma=gamma, alpha=alpha, beta=beta,
                                  pam_gamma_ratio=pam_ratio, ppl1_alpha_ratio=ppl1_ratio,
                                  weight_decay=decay, seed=seed)
                for seed in (1, 2, 3)]
        return round(sum(vals) / len(vals), 6)
    except Exception:
        return 0.0


def evaluate_genome(genome: Sequence[float], teacher: Teacher, seeds: Sequence[int],
                    turns: int, base_gain: float = 1.0) -> Dict[str, object]:
    """Held-out chat reward + fly fitness + noise N1-N5 + final physiological state."""
    vals: List[float] = []
    final: Dict[str, float] = {}
    noise_vals: List[Dict[str, float]] = []
    noise_genes = tuple(genome[11:17]) if len(genome) > 11 else None
    for seed in seeds:
        mind = MindBase(MindConfig(seed=int(seed)))
        ns = EndogenousNoise(seed=int(seed)) if noise_genes is not None else None
        if ns is not None:
            ns.configure(noise_genes)
            mind.endogenous_noise = ns
        apply_genome(mind, tuple(genome), base_gain, noise_system=ns)
        kernel = Kernel(mind)
        env = SimEnv(mind, kernel, teacher, noise_system=ns)
        result = env.run_episode(int(seed), max_turns=int(turns))
        vals.append(result.mean_reward)
        if ns is not None:
            noise_vals.append(ns.spontaneous_metrics(48))
        final = {
            "v": float(getattr(mind.affect, "v", 0.0)),
            "stress": float(getattr(mind.affect, "stress", 0.0)),
            "energy": float(mind.drives.values.get("energy", 0.0)),
            "social": float(mind.drives.values.get("social", 0.0)),
            "viability": float(mind.viability_state.get("viability", 1.0)),
        }
    noise_mean: Dict[str, float] = {}
    if noise_vals:
        keys = [k for k in noise_vals[0] if isinstance(noise_vals[0][k], (int, float))]
        for k in keys:
            noise_mean[k] = round(sum(float(x.get(k, 0.0)) for x in noise_vals) / len(noise_vals), 5)
    return {
        "chat_reward": round(sum(vals) / max(1, len(vals)), 6),
        "fly_fitness": fly_fitness(tuple(genome)),
        "noise": noise_mean,
        "final_state": {k: round(v, 4) for k, v in final.items()},
    }


def _build_dsh_client():
    try:
        from .llm_openai import OpenAICompatClient
    except Exception:
        return None
    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    base = os.getenv("LLM_BASE_URL") or "https://api.deepseek.com"
    model = os.getenv("LLM_MODEL") or "deepseek-chat"
    try:
        return OpenAICompatClient(model=model, base_url=base, api_key=key, temperature=0.8)
    except Exception:
        return None


def run_offline_evolution(
    generations: int = 10,
    scenario_count: int = 50,
    seed: int = 1,
    teacher: Optional[Teacher] = None,
    population_size: int = 6,
    eval_turns: int = 5,
    out_path: Optional[Path | str] = None,
    verbose: bool = True,
    noise: bool = True,
    noise_weight: float = 0.6,
) -> Dict[str, object]:
    t0 = time.perf_counter()
    random.seed(int(seed))
    teacher = teacher or ScriptedTeacher(n_scenarios=scenario_count, seed=seed)
    mind = MindBase(MindConfig(seed=int(seed)))
    base_gain = float(getattr(mind.autonomy, "gain", 1.0))
    evo = EvolutionExperiment(mind, noise=noise, noise_weight=noise_weight)
    if population_size and int(population_size) < len(evo.population):
        evo.population = list(evo.population[:int(population_size)])
        evo.scores = [[] for _ in evo.population]
        evo.idx = 0
        evo._apply(0)
    evo.eval_turns = max(1, int(eval_turns))
    kernel = Kernel(mind, [evo])
    env = SimEnv(mind, kernel, teacher, noise_system=evo.noise_system)

    initial = tuple(evo.population[0])
    episode = 0
    max_episodes = max(1, int(generations)) * len(evo.population) + 8
    while evo.generation < int(generations) and episode < max_episodes:
        env.run_episode(int(seed) + episode, max_turns=evo.eval_turns)
        episode += 1

    best = tuple(evo.population[0])
    holdout_seeds = list(range(10000, 10000 + max(1, int(scenario_count))))
    baseline_score = evaluate_genome(initial, teacher, holdout_seeds, evo.eval_turns, base_gain)
    evolved_score = evaluate_genome(best, teacher, holdout_seeds, evo.eval_turns, base_gain)

    out: Dict[str, object] = {
        "teacher": type(teacher).__name__,
        "seed": int(seed),
        "generations": int(evo.generation),
        "episodes": int(episode),
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "population_size": len(evo.population),
        "eval_turns": int(evo.eval_turns),
        "base_gain": base_gain,
        "genome_len": int(evo.genome_len),
        "noise_mode": bool(noise),
        "noise_weight": float(noise_weight),
        "initial_genome": [float(x) for x in initial],
        "best_genome": [float(x) for x in best],
        "history": list(evo.history),
        "archive": sorted(evo.archive.keys()),
        "holdout": {
            "scenarios": len(holdout_seeds),
            "baseline": baseline_score,
            "evolved": evolved_score,
            "delta_chat": round(evolved_score["chat_reward"] - baseline_score["chat_reward"], 6),
            "delta_fly": round(evolved_score["fly_fitness"] - baseline_score["fly_fitness"], 6),
            "delta_noise": round(float(evolved_score.get("noise", {}).get("score", 0.0)) - float(baseline_score.get("noise", {}).get("score", 0.0)), 6),
        },
        "final_state": {
            "v": round(float(getattr(mind.affect, "v", 0.0)), 4),
            "stress": round(float(getattr(mind.affect, "stress", 0.0)), 4),
            "energy": round(float(mind.drives.values.get("energy", 0.0)), 4),
            "social": round(float(mind.drives.values.get("social", 0.0)), 4),
            "viability": round(float(mind.viability_state.get("viability", 1.0)), 4),
        },
    }
    path = Path(out_path) if out_path else DATA
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    if verbose:
        print(json.dumps({
            "teacher": out["teacher"],
            "generations": out["generations"],
            "episodes": out["episodes"],
            "holdout": out["holdout"],
            "best_genome": [round(float(x), 5) for x in out["best_genome"]],
            "output": str(path),
        }, ensure_ascii=False, indent=2))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline 11/17-gene evolution with a DSH/scripted teacher")
    parser.add_argument("--generations", type=int, default=10)
    parser.add_argument("--scenarios", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--teacher", choices=["scripted", "dsh"], default="scripted")
    parser.add_argument("--population", type=int, default=6)
    parser.add_argument("--eval-turns", type=int, default=5)
    parser.add_argument("--no-noise", action="store_true", help="disable the 6 noise/criticality genes")
    parser.add_argument("--noise-weight", type=float, default=0.6)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    teacher: Teacher
    if args.teacher == "dsh":
        client = _build_dsh_client()
        teacher = DSHTeacher(client=client, n_scenarios=args.scenarios, seed=args.seed)
        if client is None:
            print("[evolve_offline] DSH client unavailable; DSHTeacher will use scripted fallback.")
    else:
        teacher = ScriptedTeacher(n_scenarios=args.scenarios, seed=args.seed)
    out_path = Path(args.out) if args.out else None
    run_offline_evolution(
        generations=args.generations,
        scenario_count=args.scenarios,
        seed=args.seed,
        teacher=teacher,
        population_size=args.population,
        eval_turns=args.eval_turns,
        out_path=out_path,
        noise=not args.no_noise,
        noise_weight=args.noise_weight,
        verbose=True,
    )


if __name__ == "__main__":
    main()
