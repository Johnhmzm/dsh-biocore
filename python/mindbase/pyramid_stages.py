"""Stagewise need pyramid for the survival gauntlet.

Only an organism with every physiology / emotion / curiosity component and
enough intelligence passes all five stages.  Each stage is a gate: the next
stage is never evaluated until the previous one is passed.
"""
from __future__ import annotations

from .survival_gauntlet import OrganismGenome, run_gauntlet


STAGES = ("physiological", "safety", "belonging", "esteem", "actualization")


def _requirements(g: OrganismGenome):
    return {
        "physiological": g.hunger and g.thirst and g.sleep and g.pain and g.immune,
        "safety": g.fear and g.pain and g.immune,
        "belonging": g.social and g.emotion_readout,
        "esteem": g.emotion_readout and g.intelligence >= 0.35,
        "actualization": g.curiosity and g.emotion_readout and g.intelligence >= 0.50,
    }


def run_pyramid(g: OrganismGenome, steps: int = 80, seed: int = 1) -> dict:
    req = _requirements(g)
    passed = []
    reason = "completed"
    for stage in STAGES:
        if not req[stage]:
            reason = "missing_" + stage
            break
        r = run_gauntlet(g, steps=steps, seed=seed, stage=len(passed) + 1)
        if not r["alive"]:
            reason = "died_in_" + stage
            break
        passed.append(stage)
    return {"completed": len(passed) == len(STAGES), "passed": passed,
            "stage": passed[-1] if passed else "", "reason": reason,
            "genome": g.__dict__}


def pyramid_suite(seeds=(1, 2, 3), steps: int = 80) -> dict:
    variants = {
        "full": OrganismGenome(),
        "no_hunger": OrganismGenome(hunger=False),
        "no_fear": OrganismGenome(fear=False),
        "no_social": OrganismGenome(social=False),
        "no_curiosity": OrganismGenome(curiosity=False),
        "no_emotion_readout": OrganismGenome(emotion_readout=False),
        "low_intelligence": OrganismGenome(intelligence=0.20),
    }
    out = {}
    for name, g in variants.items():
        rows = [run_pyramid(g, steps=steps, seed=s) for s in seeds]
        out[name] = {
            "completion_rate": round(sum(1 for r in rows if r["completed"]) / len(rows), 3),
            "stage_counts": {st: sum(1 for r in rows if st in r["passed"]) for st in STAGES},
            "max_stage": max((len(r["passed"]) for r in rows), default=0),
        }
    return out
