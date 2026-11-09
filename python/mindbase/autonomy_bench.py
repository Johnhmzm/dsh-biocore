"""Autonomy benchmark: free-running base with no chat and no LLM.

The product goal is autonomy, not chat.  This bench runs only tick events:
  - no user messages
  - no LLM calls
  - optional perturbation (energy crash / stress spike) to test recovery

Metrics:
  - spontaneous action rate / action entropy
  - viability/energy/stress/sleep
  - drive volatility
  - recovery steps after perturbation
"""
from __future__ import annotations

import math
import random
from collections import Counter
from typing import Dict, List, Optional

from .config import MindConfig
from .engine import MindBase
from .types import Event


def _entropy(counts: Dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            h -= p * math.log(p)
    return h / math.log(max(2, len(counts)))


def run_autonomy(steps: int = 240, dt: float = 3600.0, seed: int = 42,
                 perturb: bool = True, cfg: Optional[MindConfig] = None,
                 collect_series: bool = False, mortality: bool = False,
                 starvation: float = 0.0, initial_energy: Optional[float] = None,
                 damage_threshold: Optional[float] = None, energy_policy=None,
                 explore_success: float = 0.45, predator_risk: float = 0.0,
                 thirst_rate: float = 0.0, inflammation_rate: float = 0.0,
                 pain_rate: float = 0.0, physiology_params=None, emotion_weights=None,
                 regime_shift: int = 0, env_chaos: float = 0.0,
                 chaos_r: float = 3.9, env_drift: float = 0.0,
                 env_noise: float = 0.05, effect_delay: int = 0) -> Dict[str, object]:
    mind = MindBase(cfg if cfg is not None else MindConfig(seed=int(seed)))
    if initial_energy is not None:
        mind.drives.values["energy"] = max(0.0, min(1.0, float(initial_energy)))
    if energy_policy is not None:
        mind.heads["energy_policy"] = energy_policy
    if physiology_params:
        mind.physiology.set_coefficients(physiology_params)
    if emotion_weights:
        for emotion, features in dict(emotion_weights).items():
            for feat, value in dict(features).items():
                mind.physiology.set_emotion_weight(emotion, feat, value)
    threshold_start = float(getattr(mind.autonomy, "threshold", 0.0))
    damage = 0.0
    known_food = 0.0
    regime = 0
    shifts = 0
    chaos_x = 0.5
    chaos_sum = 0.0
    food_env = 0.60
    water_env = 0.70
    predator_env = 0.50
    env_food_sum = 0.0
    env_water_sum = 0.0
    env_pred_sum = 0.0
    pending_damage = []
    pending_energy = []
    delayed_applied = 0
    mind.env = {"known_food": known_food, "food_uncertainty": 1.0 - known_food, "regime": float(regime),
                "food_env": food_env, "water_env": water_env, "predator_env": predator_env}
    env_rng = random.Random(int(seed) + 9173)
    actions: Counter = Counter()
    reasons: Counter = Counter()
    rows = []
    perturb_at = max(1, int(steps) // 2) if perturb else -1
    pre_stress = 0.0
    for i in range(max(1, int(steps))):
        # apply effects that were queued earlier
        due_d = [v for t, v in pending_damage if t <= i]
        pending_damage = [(t, v) for t, v in pending_damage if t > i]
        due_e = [v for t, v in pending_energy if t <= i]
        pending_energy = [(t, v) for t, v in pending_energy if t > i]
        if due_d:
            damage += sum(due_d)
            delayed_applied += len(due_d)
        if due_e:
            mind.drives.values["energy"] = max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) + sum(due_e)))
        chaos_x = float(chaos_r) * chaos_x * (1.0 - chaos_x)
        chaos_x = max(0.0, min(1.0, chaos_x))
        chaos_sum += chaos_x
        food_env = max(0.0, min(1.0, food_env + float(env_drift) * (env_rng.gauss(0.0, float(env_noise)) + 0.05 * (0.5 - food_env))))
        water_env = max(0.0, min(1.0, water_env + float(env_drift) * (env_rng.gauss(0.0, float(env_noise)) + 0.05 * (0.5 - water_env))))
        predator_env = max(0.0, min(1.0, predator_env + float(env_drift) * (env_rng.gauss(0.0, float(env_noise)) + 0.05 * (0.5 - predator_env))))
        env_food_sum += food_env
        env_water_sum += water_env
        env_pred_sum += predator_env
        risk_eff = float(predator_risk) * (0.5 + float(env_chaos) * 1.5 * chaos_x) * (0.5 + float(env_drift) * predator_env)
        if regime_shift and i > 0 and i % int(regime_shift) == 0:
            regime = 1 - regime
            shifts += 1
            known_food = 0.0
            mind.env = {"known_food": known_food, "food_uncertainty": 1.0, "regime": float(regime)}
        if i == perturb_at:
            pre_stress = float(mind.affect.stress)
            mind.drives.values["energy"] = 0.05
            mind.affect.stress = 0.80
            mind.affect.v = -0.50
        if env_chaos > 0.0:
            damage += float(env_chaos) * 0.008 * chaos_x
        if starvation > 0.0:
            mind.drives.values["energy"] = max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) - float(starvation)))
        if thirst_rate > 0.0:
            mind.physiology.hydration = max(0.0, float(mind.physiology.hydration) - float(thirst_rate))
        if inflammation_rate > 0.0:
            mind.physiology.inflammation = min(1.0, float(mind.physiology.inflammation) + float(inflammation_rate))
        if pain_rate > 0.0:
            mind.physiology.pain = min(1.0, float(mind.physiology.pain) + float(pain_rate))
        out = mind.step(Event(kind="tick", ts=mind.t + float(dt)))
        action = str(getattr(out, "action", "silence"))
        kind = str(getattr(getattr(out, "act", None), "kind", ""))
        if kind == "forage":
            if risk_eff > 0.0 and env_rng.random() < float(risk_eff):
                mind.drives.values["energy"] = max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) - 0.05))
                mind.affect.stress = min(1.0, float(mind.affect.stress) + 0.10)
                (pending_damage.append((i + int(effect_delay), 0.12)) if effect_delay > 0 else None)
                damage = damage if effect_delay > 0 else damage + 0.12
            gain = (0.10 if known_food > 0.10 else 0.04) * (1.0 if env_drift <= 0.0 else (0.5 + food_env))
            (pending_energy.append((i + int(effect_delay), gain)) if effect_delay > 0 else None)
            mind.drives.values["energy"] = (float(mind.drives.values.get("energy", 0.0)) if effect_delay > 0 else max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) + gain)))
            known_food = max(0.0, known_food - 0.05)
            (pending_damage.append((i + int(effect_delay), 0.02)) if effect_delay > 0 else None)
            damage = damage if effect_delay > 0 else damage + 0.02
        elif kind == "explore":
            if risk_eff > 0.0 and env_rng.random() < float(risk_eff) * 0.7:
                mind.drives.values["energy"] = max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) - 0.05))
                mind.affect.stress = min(1.0, float(mind.affect.stress) + 0.12)
                (pending_damage.append((i + int(effect_delay), 0.14)) if effect_delay > 0 else None)
                damage = damage if effect_delay > 0 else damage + 0.14
            elif env_rng.random() < float(explore_success):
                (pending_energy.append((i + int(effect_delay), 0.18)) if effect_delay > 0 else None)
                mind.drives.values["energy"] = (float(mind.drives.values.get("energy", 0.0)) if effect_delay > 0 else max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) + 0.18)))
                known_food = min(1.0, known_food + 0.25)
            else:
                mind.drives.values["energy"] = max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) - 0.02))
                (pending_damage.append((i + int(effect_delay), 0.03)) if effect_delay > 0 else None)
                damage = damage if effect_delay > 0 else damage + 0.03
        elif kind == "drink":
            gain_d = 0.18 * (1.0 if env_drift <= 0.0 else (0.5 + water_env))
            mind.physiology.hydration = min(1.0, float(mind.physiology.hydration) + gain_d)
        elif kind == "heal":
            mind.physiology.inflammation = max(0.0, float(mind.physiology.inflammation) - 0.18)
            mind.physiology.pain = max(0.0, float(mind.physiology.pain) - 0.06)
        elif kind == "rest":
            mind.physiology.sleep_pressure = max(0.0, float(mind.physiology.sleep_pressure) - 0.12)
        elif kind == "socialize":
            mind.drives.values["social"] = max(0.0, min(1.0, float(mind.drives.values.get("social", 0.0)) + 0.05))
        actions[action] += 1
        reasons[str(getattr(out, "reason", ""))] += 1
        rows.append({
            "i": i,
            "action": action,
            "viability": float(mind.viability_state.get("viability", 1.0)),
            "energy": float(mind.drives.values.get("energy", 0.0)),
            "social": float(mind.drives.values.get("social", 0.0)),
            "stress": float(mind.affect.stress),
            "asleep": bool(mind.clock.asleep),
            "needs": float(mind.needs.snapshot().get("satisfaction", 0.0)) if hasattr(mind, "needs") else 0.0,
            "damage": float(damage),
            "pain": float(mind.physiology.pain),
            "viability": float(mind.viability_state.get("viability", 1.0)),
        })
        damage += max(0.0, 0.20 - float(mind.drives.values.get("energy", 0.0)))
        if mortality and (float(mind.drives.values.get("energy", 0.0)) <= 0.001 or float(mind.viability_state.get("viability", 1.0)) <= 0.001
                          or (damage_threshold is not None and damage >= float(damage_threshold))
                          or float(mind.physiology.hydration) <= 0.03
                          or float(mind.physiology.inflammation) >= 0.95
                          or float(mind.physiology.pain) >= 0.95
                          or float(mind.physiology.sleep_pressure) >= 1.40):
            break
    dead = bool(mortality and rows and (float(mind.drives.values.get("energy", 0.0)) <= 0.001
                                      or float(mind.viability_state.get("viability", 1.0)) <= 0.001
                                      or (damage_threshold is not None and damage >= float(damage_threshold))
                                      or float(mind.physiology.hydration) <= 0.03
                                      or float(mind.physiology.inflammation) >= 0.95
                                      or float(mind.physiology.pain) >= 0.95
                                      or float(mind.physiology.sleep_pressure) >= 1.40))
    viability = [r["viability"] for r in rows]
    energy = [r["energy"] for r in rows]
    stress = [r["stress"] for r in rows]
    social = [r["social"] for r in rows]
    vol = 0.0
    if len(energy) > 1:
        vol = sum(abs(energy[i] - energy[i - 1]) + abs(social[i] - social[i - 1]) for i in range(1, len(energy))) / (len(energy) - 1)
    recovery = -1
    if perturb and perturb_at > 0:
        target_stress = min(0.30, pre_stress + 0.10)
        for r in rows[perturb_at:]:
            if r["viability"] >= 0.90 and r["stress"] <= target_stress:
                recovery = int(r["i"] - perturb_at)
                break
    half = steps // 2
    return {
        "steps": len(rows),
        "actions": dict(actions),
        "reasons": dict(reasons.most_common(8)),
        "spontaneous_rate": round(1.0 - actions.get("silence", 0) / max(1, len(rows)), 4),
        "action_entropy": round(_entropy(dict(actions)), 4),
        "viability_min": round(min(viability), 4),
        "viability_mean": round(sum(viability) / len(viability), 4),
        "energy_mean": round(sum(energy) / len(energy), 4),
        "stress_mean": round(sum(stress) / len(stress), 4),
        "social_mean": round(sum(social) / len(social), 4),
        "sleep_fraction": round(sum(1 for r in rows if r["asleep"]) / max(1, len(rows)), 4),
        "drive_volatility": round(vol, 5),
        "recovery_steps": recovery,
        "perturbed": bool(perturb),
        "pre_perturb_stress": round(pre_stress, 4),
        "threshold_start": round(threshold_start, 4),
        "threshold_end": round(float(getattr(mind.autonomy, "threshold", 0.0)), 4),
        "dead": bool(dead),
        "lifespan": len(rows),
        "death_reason": ("damage" if dead and damage_threshold is not None and damage >= float(damage_threshold) else ("starvation" if dead and float(mind.drives.values.get("energy", 0.0)) <= 0.001 else ("viability" if dead else "alive"))),
        "damage": round(damage, 4),
        "known_food": round(known_food, 4),
        "regime": int(regime),
        "regime_shifts": int(shifts),
        "env_chaos": round(float(env_chaos), 4),
        "chaos_mean": round(chaos_sum / max(1, len(rows)), 4),
        "chaos_final": round(chaos_x, 4),
        "env_food_mean": round(env_food_sum / max(1, len(rows)), 4),
        "env_water_mean": round(env_water_sum / max(1, len(rows)), 4),
        "env_predator_mean": round(env_pred_sum / max(1, len(rows)), 4),
        "effect_delay": int(effect_delay),
        "delayed_pending": len(pending_damage) + len(pending_energy),
        "delayed_applied": int(delayed_applied),
        "pending_damage_sum": round(sum(v for _, v in pending_damage), 4),
        "pending_energy_sum": round(sum(v for _, v in pending_energy), 4),
        "hydration": round(float(mind.physiology.hydration), 4),
        "inflammation": round(float(mind.physiology.inflammation), 4),
        "pain": round(float(mind.physiology.pain), 4),
        "sleep_pressure": round(float(mind.physiology.sleep_pressure), 4),
        "needs": mind.needs.snapshot() if hasattr(mind, "needs") else {},
        "series": rows if collect_series else None,
    }
