"""Relationship stages for a per-speaker bond.

stranger -> acquaintance -> familiar -> trusted -> intimate
Stage is derived from bond state and uses hysteresis so it does not flap.
"""

from __future__ import annotations

from typing import Any, Dict

STAGES = ("stranger", "acquaintance", "familiar", "trusted", "intimate")
THRESHOLDS = (0.30, 0.45, 0.60, 0.75)
HYSTERESIS = 0.03

STAGE_GUIDANCE = {
    "stranger": {"max_tokens": 140, "style": "brief, polite, low disclosure"},
    "acquaintance": {"max_tokens": 180, "style": "friendly but bounded"},
    "familiar": {"max_tokens": 240, "style": "warm, shared context"},
    "trusted": {"max_tokens": 300, "style": "direct, can disagree"},
    "intimate": {"max_tokens": 360, "style": "direct, high disclosure"},
}


def score(bond: Any) -> float:
    return (
        0.50 * float(getattr(bond, "familiarity", 0.0))
        + 0.35 * float(getattr(bond, "trust", 0.0))
        + 0.15 * float(getattr(bond, "attachment", 0.0))
    )


def stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        return 0


def stage_for(bond: Any, current: str = "stranger") -> str:
    idx = stage_index(current)
    s = score(bond)
    # upgrade with hysteresis
    while idx < len(STAGES) - 1 and s >= THRESHOLDS[idx] + HYSTERESIS:
        idx += 1
    # downgrade with hysteresis
    while idx > 0 and s < THRESHOLDS[idx - 1] - HYSTERESIS:
        idx -= 1
    return STAGES[idx]


def update_stage(bond: Any) -> str:
    stage = stage_for(bond, getattr(bond, "stage", "stranger"))
    bond.stage = stage
    return stage


def guidance(stage: str) -> Dict[str, object]:
    return dict(STAGE_GUIDANCE.get(stage, STAGE_GUIDANCE["stranger"]))


def describe(stage: str) -> Dict[str, object]:
    return {
        "stage": stage if stage in STAGES else "stranger",
        "index": stage_index(stage),
        "score_band": (
            "0.00-0.30"
            if stage_index(stage) == 0
            else f"{THRESHOLDS[stage_index(stage) - 1]:.2f}+"
        ),
    }
