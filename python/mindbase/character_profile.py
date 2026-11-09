from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

BOUNDS = {
    "O": (0.0, 1.0), "C": (0.0, 1.0), "E": (0.0, 1.0), "A": (0.0, 1.0), "N": (0.0, 1.0),
    "baseline_v": (0.0, 1.0), "baseline_a": (0.0, 1.0),
    "stress_reactivity": (0.0, 1.0), "recovery_rate": (0.0, 1.0),
    "social_baseline": (0.0, 1.0), "energy_baseline": (0.0, 1.0),
    "curiosity_baseline": (0.0, 1.0), "safety_baseline": (0.0, 1.0),
    "da_baseline": (0.0, 1.0), "oa_baseline": (0.0, 1.0), "ht_baseline": (0.0, 1.0),
    "autonomy_gain": (0.0, 1.5), "learning_rate": (0.01, 0.12),
}


def merge(base: Dict[str, float], delta: Dict[str, float]) -> Dict[str, float]:
    out = {}
    for key, value in base.items():
        d = float(delta.get(key, 0.0) or 0.0)
        v = float(value) + d
        lo, hi = BOUNDS.get(key, (-1e9, 1e9))
        out[key] = round(max(lo, min(hi, v)), 4)
    return out


def load_character(root: Path) -> Dict[str, float]:
    base = json.loads((root / "base.json").read_text(encoding="utf-8"))
    delta = json.loads((root / "delta.json").read_text(encoding="utf-8"))
    effective = merge(base, delta)
    (root / "effective.json").write_text(json.dumps(effective, ensure_ascii=False, indent=2), encoding="utf-8")
    return effective
