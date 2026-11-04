"""Quality-diversity over autonomy configurations.

No chat.  Each genome is a small set of mutable parameters:
  autonomy_threshold, volition_temperature, autonomy_adapt_lr, noise_sigma

Fitness combines viability, recovery, action entropy and spontaneous rate.
The archive is behavior-indexed (spontaneous rate × action entropy), so the
loop preserves different behavioral modes instead of collapsing to one.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, asdict, replace
from typing import Dict, List, Tuple

from .autonomy_bench import run_autonomy
from .forage import ForagePolicy
from .symbolic import mutate as symbolic_mutate, random_expr, formula_stability, formula_complexity
from .config import MindConfig


@dataclass
class AutonomyGenome:
    autonomy_threshold: float = 0.90
    volition_temperature: float = 0.0
    autonomy_adapt_lr: float = 0.004
    noise_sigma: float = 0.03
    forage_weight: float = 1.0
    rest_weight: float = 0.7
    social_weight: float = 0.5
    forage_temperature: float = 0.3
    fear_weight: float = 0.5
    curiosity_weight: float = 0.3
    explore_weight: float = 0.5
    drink_weight: float = 0.6
    heal_weight: float = 0.5
    hydration_decay: float = 0.015
    sleep_accum: float = 0.035
    inflammation_gain: float = 0.002
    cortisol_gain: float = 0.60
    fear_stress: float = 0.35
    joy_v: float = 0.45
    need_physio_gate: float = 0.8
    need_safety_gate: float = 0.8
    need_belong_gate: float = 0.8
    need_actual_gate: float = 0.8
    planning: float = 0.5
    quantum_weight: float = 0.5
    quantum_gamma: float = 0.05
    quantum_dephase: float = 0.10
    world_weight: float = 0.5
    world_horizon: int = 1
    world_beam: int = 1
    emotion_social_weight: float = 0.5
    emotion_avoid_weight: float = 0.5
    emotion_explore_weight: float = 0.5
    emotion_recovery_weight: float = 0.5
    formula_expr: object = None


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def mutate(g: AutonomyGenome, rng: random.Random, scale: float = 0.15) -> AutonomyGenome:
    return AutonomyGenome(
        autonomy_threshold=_clip(g.autonomy_threshold + rng.gauss(0, scale), 0.30, 1.20),
        volition_temperature=_clip(g.volition_temperature + rng.gauss(0, scale), 0.0, 1.0),
        autonomy_adapt_lr=_clip(g.autonomy_adapt_lr * math.exp(rng.gauss(0, 0.4)), 0.0005, 0.02),
        noise_sigma=_clip(g.noise_sigma + rng.gauss(0, 0.02), 0.0, 0.20),
        forage_weight=_clip(g.forage_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        rest_weight=_clip(g.rest_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        social_weight=_clip(g.social_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        forage_temperature=_clip(g.forage_temperature * math.exp(rng.gauss(0, 0.3)), 0.05, 1.0),
        fear_weight=_clip(g.fear_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        curiosity_weight=_clip(g.curiosity_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        explore_weight=_clip(g.explore_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        drink_weight=_clip(g.drink_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        heal_weight=_clip(g.heal_weight + rng.gauss(0, 0.20), 0.0, 3.0),
        hydration_decay=_clip(g.hydration_decay + rng.gauss(0, 0.003), 0.0, 0.05),
        sleep_accum=_clip(g.sleep_accum + rng.gauss(0, 0.006), 0.0, 0.08),
        inflammation_gain=_clip(g.inflammation_gain + rng.gauss(0, 0.0005), 0.0, 0.01),
        cortisol_gain=_clip(g.cortisol_gain + rng.gauss(0, 0.08), 0.0, 1.5),
        fear_stress=_clip(g.fear_stress + rng.gauss(0, 0.04), 0.0, 1.0),
        joy_v=_clip(g.joy_v + rng.gauss(0, 0.04), 0.0, 1.0),
        need_physio_gate=_clip(g.need_physio_gate + rng.gauss(0, 0.15), 0.0, 2.5),
        need_safety_gate=_clip(g.need_safety_gate + rng.gauss(0, 0.15), 0.0, 2.5),
        need_belong_gate=_clip(g.need_belong_gate + rng.gauss(0, 0.15), 0.0, 2.5),
        need_actual_gate=_clip(g.need_actual_gate + rng.gauss(0, 0.15), 0.0, 2.5),
        planning=_clip(g.planning + rng.gauss(0, 0.08), 0.0, 1.0),
        quantum_weight=_clip(g.quantum_weight + rng.gauss(0, 0.10), 0.0, 2.0),
        quantum_gamma=_clip(g.quantum_gamma + rng.gauss(0, 0.02), 0.0, 0.5),
        quantum_dephase=_clip(g.quantum_dephase + rng.gauss(0, 0.03), 0.0, 0.8),
        emotion_social_weight=_clip(g.emotion_social_weight + rng.gauss(0, 0.12), 0.0, 2.0),
        emotion_avoid_weight=_clip(g.emotion_avoid_weight + rng.gauss(0, 0.12), 0.0, 2.0),
        emotion_explore_weight=_clip(g.emotion_explore_weight + rng.gauss(0, 0.12), 0.0, 2.0),
        emotion_recovery_weight=_clip(g.emotion_recovery_weight + rng.gauss(0, 0.12), 0.0, 2.0),
        world_weight=_clip(g.world_weight + rng.gauss(0, 0.12), 0.0, 2.0),
        world_horizon=max(1, min(5, int(round(g.world_horizon + rng.gauss(0, 0.5))))),
        world_beam=max(1, min(6, int(round(g.world_beam + rng.gauss(0, 0.5))))),
        formula_expr=(
            symbolic_mutate(g.formula_expr, rng, ("energy", "social", "stress", "hunger", "thirst", "pain", "inflammation", "sleep_pressure", "known_food", "food_uncertainty"))
            if g.formula_expr is not None and rng.random() < 0.8
            else (random_expr(rng, ("energy", "social", "stress", "hunger", "thirst", "pain", "inflammation", "sleep_pressure", "known_food", "food_uncertainty"), 2)
                  if rng.random() < 0.15 else g.formula_expr)
        ),
    )


def crossover(a: AutonomyGenome, b: AutonomyGenome, rng: random.Random) -> AutonomyGenome:
    """Sexual recombination of low-level genes; missing from mutation-only QD."""
    out = {}
    for field_name in AutonomyGenome.__dataclass_fields__:
        out[field_name] = getattr(a if rng.random() < 0.5 else b, field_name)
    child = AutonomyGenome(**out)
    # light mutation keeps crossover from being a pure copy
    return mutate(child, rng, scale=0.06)

def evaluate(g: AutonomyGenome, steps: int = 120, seed: int = 1,
             weights: Dict[str, float] | None = None, mortality: bool = False,
             starvation: float = 0.0, damage_threshold: float | None = None,
             predator_risk: float = 0.0, thirst_rate: float = 0.0,
             inflammation_rate: float = 0.0, pain_rate: float = 0.0,
             regime_shift: int = 0, env_chaos: float = 0.0, chaos_r: float = 3.9,
             effect_delay: int = 0) -> Dict[str, object]:
    cfg = MindConfig(seed=int(seed), autonomy_threshold=g.autonomy_threshold,
                     volition_temperature=g.volition_temperature,
                     autonomy_adapt_lr=g.autonomy_adapt_lr,
                     noise_sigma=g.noise_sigma)
    policy = ForagePolicy(forage_weight=g.forage_weight, rest_weight=g.rest_weight,
                          social_weight=g.social_weight, temperature=g.forage_temperature,
                          fear_weight=g.fear_weight, curiosity_weight=g.curiosity_weight,
                          explore_weight=g.explore_weight, drink_weight=g.drink_weight,
                          heal_weight=g.heal_weight, need_physio_gate=g.need_physio_gate,
                          need_safety_gate=g.need_safety_gate, need_belong_gate=g.need_belong_gate,
                          need_actual_gate=g.need_actual_gate, planning=g.planning,
                          quantum_weight=g.quantum_weight, quantum_gamma=g.quantum_gamma,
                          quantum_dephase=g.quantum_dephase,
                          emotion_social_weight=g.emotion_social_weight,
                          emotion_avoid_weight=g.emotion_avoid_weight,
                          emotion_explore_weight=g.emotion_explore_weight,
                          emotion_recovery_weight=g.emotion_recovery_weight,
                          formula_expr=g.formula_expr, world_weight=g.world_weight,
                          world_horizon=g.world_horizon, world_beam=g.world_beam,
                          rng=random.Random(int(seed)))
    phys = {"hydration_decay": g.hydration_decay, "sleep_accum": g.sleep_accum,
            "inflammation_gain": g.inflammation_gain, "cortisol_gain": g.cortisol_gain}
    emo = {"fear": {"stress": g.fear_stress}, "joy": {"vpos": g.joy_v}}
    r = run_autonomy(steps=steps, cfg=cfg, perturb=True, mortality=mortality, collect_series=True,
                     starvation=starvation, damage_threshold=damage_threshold,
                     energy_policy=policy, predator_risk=predator_risk,
                     thirst_rate=thirst_rate, inflammation_rate=inflammation_rate, pain_rate=pain_rate,
                     physiology_params=phys, emotion_weights=emo, regime_shift=regime_shift,
                     env_chaos=env_chaos, chaos_r=chaos_r, effect_delay=effect_delay)
    recovery = float(r.get("recovery_steps", -1))
    rec_score = 0.0 if recovery < 0 else 1.0 / (1.0 + recovery)
    viability = float(r.get("viability_mean", 0.0))
    vmin = float(r.get("viability_min", 0.0))
    spont = float(r.get("spontaneous_rate", 0.0))
    entropy = float(r.get("action_entropy", 0.0))
    w = weights or {"viability": 1.0, "recovery": 0.5, "entropy": 0.3, "spont": 0.2, "penalty": 0.5}
    fitness = (float(w.get("viability", 1.0)) * viability
               + float(w.get("recovery", 0.5)) * rec_score
               + float(w.get("entropy", 0.3)) * entropy
               + float(w.get("spont", 0.2)) * spont
               - float(w.get("penalty", 0.5)) * (1.0 if vmin < 0.5 else 0.0))
    died = bool(r.get("dead", False))
    lifespan = float(r.get("lifespan", steps)) / max(1.0, float(steps))
    fitness += 0.30 * lifespan - (0.50 if died else 0.0)
    needs = r.get("needs") or {}
    deficits = needs.get("deficits") or {}
    phys = float(deficits.get("physiological", 0.0))
    safety = float(deficits.get("safety", 0.0))
    belonging = float(deficits.get("belonging", 0.0))
    esteem = float(deficits.get("esteem", 0.0))
    actual = float(deficits.get("actualization", 0.0))
    lower = max(phys, safety)
    higher = max(belonging, esteem, actual)
    # stepwise gating: lower unmet needs suppress higher-level reward
    fitness += 0.25 * (1.0 - lower) - 0.35 * lower - 0.15 * higher
    # entropy cannot be farmed when lower needs are unmet
    fitness += 0.25 * float(needs.get("satisfaction", 0.0)) - 0.30 * entropy * lower
    formula = g.formula_expr
    f_complexity = formula_complexity(formula) if formula is not None else 0
    f_stability = formula_stability(formula) if formula is not None else 1.0
    if formula is not None:
        fitness -= 0.002 * f_complexity + 0.25 * (1.0 - f_stability)
    # discounted future effects: pending harm/help is not free
    fitness -= 0.35 * float(r.get("pending_damage_sum", 0.0))
    fitness += 0.12 * float(r.get("pending_energy_sum", 0.0))
    # multi-horizon: late needs matter more than early transient needs
    series = r.get("series") or []
    early_needs = late_needs = 0.0
    future_trend = 0.0
    if len(series) >= 5:
        k = max(1, len(series) // 5)
        early_needs = sum(float(x.get("needs", 0.0)) for x in series[:k]) / k
        late_needs = sum(float(x.get("needs", 0.0)) for x in series[-k:]) / k
        future_trend = late_needs - early_needs
        fitness += 0.20 * late_needs - 0.20 * max(0.0, -future_trend)
    vbin = max(0, min(3, int(viability * 4)))
    rbin = 0 if recovery < 0 else max(0, min(2, int(recovery)))
    descriptor = (min(4, int(spont * 5)), min(4, int(entropy * 5)), vbin, rbin)
    return {"genome": asdict(g), "fitness": round(fitness, 5), "descriptor": descriptor,
            "viability": round(viability, 4), "viability_min": round(vmin, 4),
            "spontaneous": round(spont, 4), "entropy": round(entropy, 4),
            "recovery_steps": int(recovery), "threshold_end": r.get("threshold_end"),
            "dead": died, "lifespan": int(r.get("lifespan", steps)), "damage": r.get("damage", 0.0),
            "needs_dominant": needs.get("dominant"), "needs_satisfaction": needs.get("satisfaction"),
            "formula_complexity": f_complexity, "formula_stability": round(float(f_stability), 3),
            "pending_damage_sum": r.get("pending_damage_sum", 0.0),
            "pending_energy_sum": r.get("pending_energy_sum", 0.0),
            "early_needs": round(float(early_needs), 4), "late_needs": round(float(late_needs), 4),
            "future_trend": round(float(future_trend), 4)}


def run_qd(generations: int = 10, population: int = 10, steps: int = 120, seed: int = 1,
           weights: Dict[str, float] | None = None, mortality: bool = False,
           starvation: float = 0.0, damage_threshold: float | None = None,
           archive_path=None, predator_risk: float = 0.0, thirst_rate: float = 0.0,
           inflammation_rate: float = 0.0, pain_rate: float = 0.0,
           regime_shift: int = 0, initials: List[AutonomyGenome] | None = None,
           env_chaos: float = 0.0, chaos_r: float = 3.9,
           effect_delay: int = 0, burst_prob: float = 0.0) -> Dict[str, object]:
    rng = random.Random(int(seed))
    archive: Dict[Tuple[int, ...], Dict[str, object]] = {}
    if archive_path:
        try:
            from pathlib import Path
            rows = json.loads(Path(archive_path).read_text(encoding="utf-8"))
            for row in rows if isinstance(rows, list) else []:
                key = tuple(int(x) for x in row.get("descriptor", []))
                if key:
                    archive[key] = row
        except Exception:
            archive = {}
    initial = list(initials) if initials else [
        AutonomyGenome(0.9, 0.0, 0.004, 0.03),
        AutonomyGenome(0.8, 0.0, 0.004, 0.03),
        AutonomyGenome(0.7, 0.1, 0.004, 0.03),
        AutonomyGenome(0.9, 0.2, 0.006, 0.05),
        AutonomyGenome(0.6, 0.3, 0.004, 0.05),
        AutonomyGenome(0.75, 0.0, 0.008, 0.08),
    ]
    for g in initial[:max(1, population)]:
        r = evaluate(g, steps=steps, seed=seed, weights=weights, mortality=mortality,
                         starvation=starvation, damage_threshold=damage_threshold,
                         predator_risk=predator_risk, thirst_rate=thirst_rate,
                         inflammation_rate=inflammation_rate, pain_rate=pain_rate,
                         regime_shift=regime_shift, env_chaos=env_chaos, chaos_r=chaos_r, effect_delay=effect_delay)
        k = tuple(r["descriptor"])  # type: ignore[arg-type]
        if k not in archive or r["fitness"] > archive[k]["fitness"]:
            archive[k] = r
    history = []
    for gen in range(max(1, int(generations))):
        elites = list(archive.values())
        candidates: List[AutonomyGenome] = []
        for e in elites:
            if rng.random() < float(burst_prob):
                candidates.append(burst_mutate(AutonomyGenome(**e["genome"]), rng))  # type: ignore[arg-type]
            elif rng.random() < 0.35 and len(elites) >= 2:
                other = rng.choice(elites)
                candidates.append(crossover(AutonomyGenome(**e["genome"]),  # type: ignore[arg-type]
                                           AutonomyGenome(**other["genome"]), rng))  # type: ignore[arg-type]
            else:
                candidates.append(mutate(AutonomyGenome(**e["genome"]), rng))  # type: ignore[arg-type]
        while len(candidates) < max(1, population):
            base = AutonomyGenome(**rng.choice(elites)["genome"]) if elites else AutonomyGenome()
            candidates.append(mutate(base, rng, scale=0.25))
        for g in candidates:
            r = evaluate(g, steps=steps, seed=seed, weights=weights, mortality=mortality,
                         starvation=starvation, damage_threshold=damage_threshold,
                         predator_risk=predator_risk, thirst_rate=thirst_rate,
                         inflammation_rate=inflammation_rate, pain_rate=pain_rate,
                         regime_shift=regime_shift, env_chaos=env_chaos, chaos_r=chaos_r, effect_delay=effect_delay)
            k = tuple(r["descriptor"])  # type: ignore[arg-type]
            if k not in archive or r["fitness"] > archive[k]["fitness"]:
                archive[k] = r
        best_now = max(archive.values(), key=lambda e: e["fitness"]) if archive else {}
        history.append({
            "generation": gen + 1,
            "archive": len(archive),
            "best": round(float(best_now.get("fitness", 0.0)), 5),
            "best_planning": round(float((best_now.get("genome") or {}).get("planning", 0.0)), 3),
            "best_lifespan": int(best_now.get("lifespan", 0)),
            "best_needs": best_now.get("needs_satisfaction"),
            "descriptors": [list(k) for k in archive.keys()],
        })
    best = max(archive.values(), key=lambda e: e["fitness"]) if archive else None
    if archive_path:
        try:
            from pathlib import Path
            rows = sorted(archive.values(), key=lambda e: float(e.get("fitness", 0.0)), reverse=True)
            Path(archive_path).parent.mkdir(parents=True, exist_ok=True)
            Path(archive_path).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return {"archive_size": len(archive), "best": best, "history": history,
            "descriptors": [list(k) for k in archive.keys()], "archive_path": str(archive_path) if archive_path else ""}



def burst_mutate(g: AutonomyGenome, rng: random.Random, k: int | None = None) -> AutonomyGenome:
    """Saltation / punctuated mutation: a few genes take large jumps.

    Normal mutate() is local; this adds heavy-tailed structural variation and
    occasional formula replacement.  It is a mutation operator, not a rule.
    """
    bounds = {
        "autonomy_threshold": (0.30, 1.20), "volition_temperature": (0.0, 1.0),
        "autonomy_adapt_lr": (0.0005, 0.02), "noise_sigma": (0.0, 0.20),
        "forage_weight": (0.0, 3.0), "rest_weight": (0.0, 3.0), "social_weight": (0.0, 3.0),
        "forage_temperature": (0.05, 1.0), "fear_weight": (0.0, 3.0),
        "curiosity_weight": (0.0, 3.0), "explore_weight": (0.0, 3.0),
        "drink_weight": (0.0, 3.0), "heal_weight": (0.0, 3.0),
        "hydration_decay": (0.0, 0.05), "sleep_accum": (0.0, 0.08),
        "inflammation_gain": (0.0, 0.01), "cortisol_gain": (0.0, 1.5),
        "fear_stress": (0.0, 1.0), "joy_v": (0.0, 1.0),
        "need_physio_gate": (0.0, 2.5), "need_safety_gate": (0.0, 2.5),
        "need_belong_gate": (0.0, 2.5), "need_actual_gate": (0.0, 2.5),
        "planning": (0.0, 1.0), "quantum_weight": (0.0, 2.0),
        "quantum_gamma": (0.0, 0.5), "quantum_dephase": (0.0, 0.8),
        "emotion_social_weight": (0.0, 2.0), "emotion_avoid_weight": (0.0, 2.0),
        "emotion_explore_weight": (0.0, 2.0), "emotion_recovery_weight": (0.0, 2.0),
        "world_weight": (0.0, 2.0),
    }
    d = {name: getattr(g, name) for name in AutonomyGenome.__dataclass_fields__}
    names = list(bounds)
    n = int(k) if k is not None else rng.randint(3, 8)
    for name in rng.sample(names, min(n, len(names))):
        lo, hi = bounds[name]
        if rng.random() < 0.45:
            d[name] = rng.uniform(lo, hi)
        else:
            d[name] = _clip(float(d[name]) + rng.gauss(0.0, (hi - lo) * 0.35), lo, hi)
    d["world_horizon"] = max(1, min(5, int(round(g.world_horizon + rng.gauss(0, 1.5)))))
    d["world_beam"] = max(1, min(6, int(round(g.world_beam + rng.gauss(0, 1.5)))))
    if rng.random() < 0.35:
        d["formula_expr"] = random_expr(rng, ("energy", "social", "stress", "hunger", "thirst", "pain", "inflammation", "sleep_pressure", "known_food", "food_uncertainty"), rng.choice([2, 3]))
    return AutonomyGenome(**d)
