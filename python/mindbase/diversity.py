"""Diversity / chaos metrics for free-running autonomy.

We do not claim literal chaos; we measure:
  - action entropy across runs
  - pairwise Jensen-Shannon divergence of action distributions
  - permutation entropy of energy/social trajectories
  - range of state outcomes
"""
from __future__ import annotations

import math
from collections import Counter
from itertools import permutations
from typing import Dict, List, Sequence, Tuple

from .autonomy_bench import run_autonomy
from .config import MindConfig


def _norm(c: Dict[str, int]) -> Dict[str, float]:
    total = sum(c.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in c.items()}


def js_divergence(p: Dict[str, float], q: Dict[str, float]) -> float:
    keys = set(p) | set(q)
    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}

    def kl(a, b):
        return sum(v * math.log2(v / max(1e-12, b.get(k, 1e-12))) for k, v in a.items() if v > 0)

    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def permutation_entropy(series: Sequence[float], order: int = 3) -> float:
    vals = [float(x) for x in series]
    if len(vals) < order + 1:
        return 0.0
    patterns = Counter()
    for i in range(len(vals) - order + 1):
        window = vals[i:i + order]
        rank = tuple(sorted(range(order), key=lambda j: window[j]))
        patterns[rank] += 1
    total = sum(patterns.values())
    h = -sum((c / total) * math.log2(c / total) for c in patterns.values())
    return round(h / math.log2(math.factorial(order)), 4)


def run_diversity(steps: int = 120, seeds: Sequence[int] = (1, 2, 3, 4, 5),
                  temperatures: Sequence[float] = (0.0, 0.15, 0.35)) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for temp in temperatures:
        dists = []
        entropies = []
        perm = []
        ranges = []
        for seed in seeds:
            cfg = MindConfig(seed=int(seed), volition_temperature=float(temp))
            r = run_autonomy(steps=steps, cfg=cfg, perturb=False, collect_series=True)
            d = _norm(r["actions"])
            dists.append(d)
            entropies.append(float(r["action_entropy"]))
            rows = r.get("series") or []
            perm.append(permutation_entropy([x["energy"] for x in rows] + [x["social"] for x in rows], order=3))
            ranges.append((float(r["viability_min"]), float(r["viability_mean"]), float(r["stress_mean"])))
        jsds = []
        for i in range(len(dists)):
            for j in range(i + 1, len(dists)):
                jsds.append(js_divergence(dists[i], dists[j]))
        out[str(temp)] = {
            "action_entropy_mean": round(sum(entropies) / max(1, len(entropies)), 4),
            "jsd_mean": round(sum(jsds) / max(1, len(jsds)), 4),
            "jsd_max": round(max(jsds) if jsds else 0.0, 4),
            "permutation_entropy_mean": round(sum(perm) / max(1, len(perm)), 4),
            "viability_min": round(min(x[0] for x in ranges), 4),
            "viability_mean": round(sum(x[1] for x in ranges) / max(1, len(ranges)), 4),
            "stress_mean": round(sum(x[2] for x in ranges) / max(1, len(ranges)), 4),
        }
    return out


def run_heterogeneous_mixture(steps: int = 120, seeds: Sequence[int] = (1, 2),
                              temperatures: Sequence[float] = (0.0, 0.15, 0.35),
                              thresholds: Sequence[float] = (0.6, 0.7, 0.8, 0.9)) -> Dict[str, object]:
    """Mix different configs, not just seeds: the diversity of the policy family."""
    dists = []
    entropies = []
    configs = []
    for temp in temperatures:
        for th in thresholds:
            for seed in seeds:
                cfg = MindConfig(seed=int(seed), volition_temperature=float(temp), autonomy_threshold=float(th))
                r = run_autonomy(steps=steps, cfg=cfg, perturb=False)
                dists.append(_norm(r["actions"]))
                entropies.append(float(r["action_entropy"]))
                configs.append({"temperature": temp, "threshold": th, "seed": seed})
    jsds = []
    for i in range(len(dists)):
        for j in range(i + 1, len(dists)):
            jsds.append(js_divergence(dists[i], dists[j]))
    return {
        "n_configs": len(configs),
        "action_entropy_mean": round(sum(entropies) / max(1, len(entropies)), 4),
        "action_entropy_max": round(max(entropies) if entropies else 0.0, 4),
        "jsd_mean": round(sum(jsds) / max(1, len(jsds)), 4),
        "jsd_max": round(max(jsds) if jsds else 0.0, 4),
        "configs": configs,
    }
