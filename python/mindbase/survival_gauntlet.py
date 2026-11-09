"""Survival gauntlet: only complete physiology + emotion + intelligence survives.

This is an ablation harness, not a behavioural rule for the base.  It tests the
design requirement: remove any human-physiology / emotion / cognition component
and the organism should fail under a hard environment.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, asdict


@dataclass
class OrganismGenome:
    hunger: bool = True
    thirst: bool = True
    sleep: bool = True
    pain: bool = True
    fear: bool = True
    social: bool = True
    curiosity: bool = True
    immune: bool = True
    emotion_readout: bool = True
    intelligence: float = 1.0


def _choose(scores, rng, intelligence):
    # low intelligence makes noisy/random choices; high intelligence is more
    # likely to pick the currently best action
    if rng.random() > max(0.05, min(1.0, intelligence)):
        return rng.randrange(len(scores))
    return max(range(len(scores)), key=lambda i: scores[i])


def run_gauntlet(g: OrganismGenome, steps: int = 120, seed: int = 1, stage: int = 5) -> dict:
    rng = random.Random(int(seed))
    energy, hydration = 0.80, 0.80
    sleep_pressure, pain, inflammation = 0.20, 0.0, 0.0
    social, damage = 0.60, 0.0
    for t in range(max(1, int(steps))):
        predator = rng.random() < 0.18 if stage >= 2 else False
        food = rng.random() < 0.55
        water = rng.random() < 0.70
        scores = [
            (1.0 - energy) * (1.0 if g.hunger else 0.0),
            (1.0 - hydration) * (1.0 if g.thirst else 0.0),
            sleep_pressure * 1.2 * (1.0 if g.sleep else 0.0) + pain * 0.3 * (1.0 if g.pain else 0.0),
            inflammation * 1.2 * (1.0 if g.immune else 0.0),
            (1.0 - social) * (1.0 if g.social else 0.0),
            (1.0 if predator else 0.0) * (1.0 if g.fear else 0.0),
            (0.5 + (1.0 - energy)) * (0.4 + 0.6 * float(g.intelligence)) * (1.0 if g.emotion_readout else 0.0),
        ]
        if not g.emotion_readout and stage >= 3:
            action = rng.randrange(6)
        else:
            action = _choose(scores, rng, g.intelligence)
        if action == 0:
            energy = min(1.0, energy + (0.12 if food else -0.02))
            damage += 0.0 if food else 0.01
        elif action == 1:
            hydration = min(1.0, hydration + (0.16 if water else -0.02))
        elif action == 2:
            sleep_pressure = max(0.0, sleep_pressure - 0.20)
            energy = min(1.0, energy + 0.03)
            pain = max(0.0, pain - 0.01)
        elif action == 3:
            inflammation = max(0.0, inflammation - 0.18)
            pain = max(0.0, pain - 0.02)
        elif action == 4:
            social = min(1.0, social + 0.16)
        elif action == 5:
            if predator:
                damage = max(0.0, damage - 0.01)
        elif action == 6:
            if rng.random() < 0.30 + 0.45 * float(g.intelligence):
                energy = min(1.0, energy + 0.14)
                food = True
            else:
                damage += 0.03
        energy = max(0.0, energy - 0.025)
        hydration = max(0.0, hydration - 0.028)
        sleep_pressure = min(1.5, sleep_pressure + 0.024)
        social = max(0.0, social - 0.020)
        inflammation = max(0.0, inflammation + 0.020 * (1.0 if not g.immune else 0.35))
        if not g.social and stage >= 3:
            damage += 0.022
        if not g.pain and stage >= 1:
            damage += 0.015
        if predator and not g.fear:
            damage += 0.12
        if not g.hunger:
            energy = max(0.0, energy - 0.040)
        if not g.thirst:
            hydration = max(0.0, hydration - 0.014)
        if not g.sleep:
            sleep_pressure = min(1.5, sleep_pressure + 0.014)
        if energy <= 0.0 or hydration <= 0.0 or damage >= 1.0 or inflammation >= 1.0 or sleep_pressure >= 1.45:
            return {"alive": False, "lifespan": t + 1, "genome": asdict(g),
                    "energy": round(energy, 3), "hydration": round(hydration, 3),
                    "damage": round(damage, 3), "inflammation": round(inflammation, 3),
                    "sleep_pressure": round(sleep_pressure, 3), "social": round(social, 3)}
    return {"alive": True, "lifespan": steps, "genome": asdict(g),
            "energy": round(energy, 3), "hydration": round(hydration, 3),
            "damage": round(damage, 3), "inflammation": round(inflammation, 3),
            "sleep_pressure": round(sleep_pressure, 3), "social": round(social, 3)}


def ablation_suite(seeds=(1, 2, 3, 4, 5), steps: int = 120) -> dict:
    full = OrganismGenome()
    variants = {
        "full": full,
        "no_hunger": OrganismGenome(hunger=False),
        "no_thirst": OrganismGenome(thirst=False),
        "no_sleep": OrganismGenome(sleep=False),
        "no_pain": OrganismGenome(pain=False),
        "no_fear": OrganismGenome(fear=False),
        "no_social": OrganismGenome(social=False),
        "no_immune": OrganismGenome(immune=False),
        "no_curiosity": OrganismGenome(curiosity=False),
        "no_emotion_readout": OrganismGenome(emotion_readout=False),
        "low_intelligence": OrganismGenome(intelligence=0.15),
    }
    out = {}
    for name, g in variants.items():
        rows = [run_gauntlet(g, steps=steps, seed=s) for s in seeds]
        out[name] = {"survival_rate": round(sum(1 for r in rows if r["alive"]) / len(rows), 3),
                     "mean_lifespan": round(sum(r["lifespan"] for r in rows) / len(rows), 1)}
    return out
