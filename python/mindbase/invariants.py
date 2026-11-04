from __future__ import annotations

from typing import Dict, List

from . import defaults


def check(mb) -> List[str]:
    """Return a list of invariant violations (empty = healthy)."""
    errors: List[str] = []

    def num(name: str, value: float, lo: float, hi: float) -> None:
        if not (lo - 1e-9 <= value <= hi + 1e-9):
            errors.append(f"{name}={value} out of [{lo},{hi}]")

    num("affect.v", mb.affect.v, -1.0, 1.0)
    num("affect.a", mb.affect.a, 0.0, 1.0)
    num("affect.stress", mb.affect.stress, 0.0, 1.0)
    num("affect.scar", mb.affect.scar, 0.0, 1.0)
    num("affect.mood", mb.affect.mood, -1.0, 1.0)
    num("will.willpower", mb.will.willpower, 0.0, 1.0)
    num("meta.confidence", mb.meta.confidence, 0.0, 1.0)
    num("self.identity", mb.self_model.identity, 0.0, 1.0)
    num("self.continuity", mb.self_model.continuity, 0.0, 1.0)
    num("self.agency", mb.self_model.agency, 0.0, 1.0)
    for name, value in mb.drives.values.items():
        num(f"drives.{name}", value, 0.0, 1.0)

    if len(mb.wm.slots) > mb.cfg.wm_capacity:
        errors.append("working memory over capacity")
    if len(mb.memory.items) > mb.cfg.memory_capacity:
        errors.append("episodic memory over capacity")
    if len(mb.goals.items) > defaults.MAX_GOALS:
        errors.append("too many active goals")
    if len(mb.mb.kc.encode("__invariant_probe__")) != defaults.KC_ACTIVE:
        errors.append("KC sparsity drift")

    for name, mbon in (("reward", mb.mb.mbon_reward), ("punishment", mb.mb.mbon_punishment)):
        if any(abs(w) > 1.0 + 1e-9 for w in mbon.w):
            errors.append(f"MBON {name} weight out of bounds")

    return errors


def assert_ok(mb) -> None:
    errors = check(mb)
    if errors:
        raise AssertionError("; ".join(errors))


def health(mb) -> Dict[str, object]:
    errors = check(mb)
    return {"ok": not errors, "errors": errors}
