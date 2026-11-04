from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .noise import clip


@dataclass
class ExpressionPolicy:
    max_tokens: int
    directness: float
    disclosure: float
    guard: float
    style: str
    guidance: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "max_tokens": self.max_tokens,
            "directness": round(self.directness, 3),
            "disclosure": round(self.disclosure, 3),
            "guard": round(self.guard, 3),
            "style": self.style,
            "guidance": self.guidance,
        }


def policy_for(persona, stage_tokens: int = 300) -> ExpressionPolicy:
    extra = getattr(persona, "extra", {}) or {}
    expressivity = clip(float(extra.get("expressivity", 0.5)), 0.0, 1.0)
    assertiveness = clip(float(extra.get("assertiveness", 0.5)), 0.0, 1.0)
    disclosure = clip(float(extra.get("self_disclosure", 0.5)), 0.0, 1.0)
    masking = clip(float(extra.get("masking", 0.0)), 0.0, 1.0)

    factor = 0.70 + 0.60 * expressivity  # 0.5 -> 1.0
    tokens = int(clip(stage_tokens * factor, 80, 420))
    if masking >= 0.60 or expressivity <= 0.30:
        style = "guarded"
        guidance = "keep it short; avoid over-disclosure"
    elif expressivity >= 0.70:
        style = "expressive"
        guidance = "allow warmth and detail"
    else:
        style = "balanced"
        guidance = "natural, neither guarded nor expansive"
    return ExpressionPolicy(
        max_tokens=tokens,
        directness=assertiveness,
        disclosure=disclosure,
        guard=masking,
        style=style,
        guidance=guidance,
    )
