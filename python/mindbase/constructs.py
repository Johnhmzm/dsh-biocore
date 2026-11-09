from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from .noise import clip

DEFAULT_PATH = Path(__file__).with_name("constructs.json")

BOUNDS: Dict[str, Tuple[float, float]] = {
    "O": (0.0, 1.0),
    "C": (0.0, 1.0),
    "E": (0.0, 1.0),
    "A": (0.0, 1.0),
    "N": (0.0, 1.0),
    "baseline_v": (-1.0, 1.0),
    "baseline_a": (0.0, 1.0),
    "stress_reactivity": (0.0, 1.0),
    "recovery_rate": (0.0, 1.0),
    "social_baseline": (0.0, 1.0),
    "energy_baseline": (0.0, 1.0),
    "curiosity_baseline": (0.0, 1.0),
    "safety_baseline": (0.0, 1.0),
    "da_baseline": (0.0, 1.0),
    "oa_baseline": (0.0, 1.0),
    "ht_baseline": (0.0, 1.0),
    "autonomy_gain": (0.0, 1.5),
    "learning_rate": (0.01, 0.12),
    "circadian_phase": (-0.10, 0.10),
}


class ConstructRegistry:
    """Declarative construct -> numeric deltas.

    Definitions are data (JSON), not if/else branches. Register a new construct
    at runtime without touching the engine.
    """

    def __init__(self, path: Optional[Path] = None, definitions: Optional[Dict[str, dict]] = None):
        self.constructs: Dict[str, dict] = {}
        target = path or DEFAULT_PATH
        if target.exists():
            self.load(target)
        if definitions:
            self.register_many(definitions)

    def load(self, path: Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.register_many(data)

    def register(self, name: str, definition: dict) -> None:
        if "deltas" not in definition or not isinstance(definition["deltas"], dict):
            raise ValueError(f"construct {name} needs a deltas dict")
        self.constructs[name] = dict(definition)

    def register_many(self, definitions: Dict[str, dict]) -> None:
        for name, definition in definitions.items():
            self.register(name, definition)

    def has(self, name: str) -> bool:
        return name in self.constructs

    def apply(self, numbers: Dict[str, float], scores: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, float]]:
        out = dict(numbers)
        extra: Dict[str, float] = {}
        for name, raw_score in scores.items():
            definition = self.constructs.get(name)
            if definition is None:
                continue
            score = clip(float(raw_score), 0.0, 1.0)
            for target, delta in definition["deltas"].items():
                amount = score * float(delta)
                if target.startswith("extra."):
                    key = target.split(".", 1)[1]
                    extra[key] = extra.get(key, 0.5) + amount
                elif target in out:
                    out[target] = out[target] + amount
        for key, (lo, hi) in BOUNDS.items():
            if key in out:
                out[key] = clip(float(out[key]), lo, hi)
        for key in list(extra):
            extra[key] = clip(extra[key], 0.0, 1.0)
        return out, extra

    def snapshot(self) -> Dict[str, dict]:
        return {name: {"label": d.get("label", name), "description": d.get("description", "")} for name, d in self.constructs.items()}
