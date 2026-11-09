"""Human-referenced standard scores.

Human standards are multidimensional and age-dependent.  This module only
provides a normalization adapter: standard score = 100 + 15 * z, as used by
WAIS/MSCEIT-style instruments.  It does NOT claim the base is human.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


def standard_score(raw: float, mean: float, sd: float) -> float:
    sd = max(1e-9, float(sd))
    return 100.0 + 15.0 * (float(raw) - float(mean)) / sd


def band(score: float) -> str:
    s = float(score)
    if s < 70:
        return "very_low"
    if s < 85:
        return "low"
    if s < 115:
        return "typical"
    if s < 130:
        return "high"
    return "very_high"


@dataclass
class HumanStandardReport:
    domains: Dict[str, Dict[str, object]] = field(default_factory=dict)

    def add(self, domain: str, raw: float, mean: float, sd: float,
            instrument: str = "") -> None:
        s = standard_score(raw, mean, sd)
        self.domains[str(domain)] = {
            "raw": round(float(raw), 4),
            "mean": float(mean), "sd": float(sd),
            "standard_score": round(s, 2),
            "band": band(s),
            "instrument": str(instrument),
        }

    def overall(self) -> float:
        if not self.domains:
            return 100.0
        vals: List[float] = [float(v["standard_score"]) for v in self.domains.values()]
        return round(sum(vals) / len(vals), 2)

    def snapshot(self) -> Dict[str, object]:
        return {"domains": dict(self.domains), "overall": self.overall()}
