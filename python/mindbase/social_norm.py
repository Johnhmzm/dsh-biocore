"""Numeric social norm / guilt / repair loop.

All learning/decision state is numeric. No emotion labels or keyword rules are
used for decisions; text exists only in provenance/evidence fields.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


@dataclass
class Norm:
    id: str
    behavior_vec: List[float] = field(default_factory=list)
    source_id: str = ""
    scope: str = "private"  # only routing, not decision
    legitimacy: float = 0.5
    severity: float = 0.5
    strength: float = 0.0
    violations: int = 0
    repairs: int = 0
    last_violation: float = 0.0
    last_repair: float = 0.0
    active: bool = True

    def to_dict(self) -> Dict[str, object]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "Norm":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Appraisal:
    legitimacy: float = 0.5
    severity: float = 0.5
    blame: float = 1.0
    intent: float = 0.0
    internal_attribution: float = 0.5
    repair_expectation: float = 0.5
    relationship_certainty: float = 1.0
    coping_potential: float = 0.5
    norm_compatibility: float = 0.5
    norm_strength: float = 0.0
    confidence: float = 0.0
    guilt: float = 0.0
    guilt_social: float = 0.0
    guilt_self: float = 0.0
    shame: float = 0.0
    threat: float = 0.0

    def compute(self) -> "Appraisal":
        legit = _clip(self.legitimacy)
        sev = _clip(self.severity)
        blame = _clip(self.blame)
        intent = _clip(self.intent)
        internal = _clip(self.internal_attribution)
        repair_exp = _clip(self.repair_expectation)
        rel = _clip(self.relationship_certainty)
        coping = _clip(self.coping_potential)
        norm_comp = _clip(self.norm_compatibility)
        norm = _clip(self.norm_strength)
        conf = _clip(self.confidence)
        self.guilt_social = _clip(legit * sev * blame * (0.5 + 0.5 * intent)
                                  * internal * rel * (1.0 - 0.4 * norm_comp))
        self.guilt_self = _clip(sev * internal * norm * conf)
        self.guilt = _clip(0.7 * self.guilt_social + 0.3 * self.guilt_self)
        self.shame = _clip(sev * internal * (0.5 + 0.5 * self.guilt)
                           * (0.7 + 0.3 * rel) * (1.0 - 0.3 * coping))
        self.threat = _clip(sev * legit * (1.0 - repair_exp) * rel * (1.0 - 0.5 * coping))
        return self


@dataclass
class Repair:
    norm_id: str
    effort: float
    accepted: bool
    at: float = 0.0
    guilt_before: float = 0.0
    guilt_after: float = 0.0


class NormStore:
    def __init__(self, path: Path | str = "mindbase/data/social_norms.json",
                 lr: float = 0.35, decay_tau_days: float = 30.0):
        self.path = Path(path)
        self.lr = float(lr)
        self.decay_tau = float(decay_tau_days) * 86400.0
        self.norms: Dict[str, Norm] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for d in data.get("norms", []):
                n = Norm.from_dict(d)
                self.norms[n.id] = n
        except Exception:
            self.norms = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"norms": [n.to_dict() for n in self.norms.values()]},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def add(self, norm_id: str, behavior_vec: Optional[List[float]] = None,
            source_id: str = "", legitimacy: float = 0.5,
            severity: float = 0.5, strength: float = 0.0, scope: str = "private") -> Norm:
        n = Norm(id=str(norm_id), behavior_vec=list(behavior_vec or []),
                 source_id=str(source_id), scope=str(scope),
                 legitimacy=_clip(legitimacy), severity=_clip(severity),
                 strength=_clip(strength))
        self.norms[n.id] = n
        self.save()
        return n

    def get(self, norm_id: str) -> Optional[Norm]:
        return self.norms.get(str(norm_id))

    def violate(self, norm_id: str, appraisal: Appraisal, now: float = 0.0,
                source_id: str = "", scope: str = "") -> Norm:
        n = self.get(norm_id)
        if n is None:
            n = self.add(norm_id, source_id=source_id, scope=scope or "private")
        conf = 1.0 - math.exp(-(max(0, n.violations) + 1) / 3.0)
        appraisal.confidence = conf
        appraisal.norm_strength = n.strength
        a = appraisal.compute()
        n.strength = _clip(n.strength + self.lr * conf * a.guilt * (0.5 + 0.5 * a.severity))
        n.violations += 1
        n.last_violation = float(now or time.time())
        self.save()
        return n

    def repair(self, norm_id: str, effort: float, accepted: bool,
               guilt: float = 0.0, now: float = 0.0) -> Repair:
        n = self.get(norm_id)
        before = float(guilt)
        after = before
        if n is not None and accepted:
            n.strength = _clip(n.strength * (0.85 - 0.25 * _clip(effort)))
            n.repairs += 1
            n.last_repair = float(now or time.time())
            after = _clip(before * (1.0 - 0.7 * _clip(effort)))
            self.save()
        return Repair(norm_id=str(norm_id), effort=_clip(effort), accepted=bool(accepted),
                      at=float(now or time.time()), guilt_before=before, guilt_after=after)

    def active_norms(self, scope: str = "", source_id: str = "") -> list:
        out = []
        for n in self.norms.values():
            if not n.active or n.strength <= 0:
                continue
            if scope and n.scope != scope:
                continue
            if source_id and n.source_id not in ("", source_id):
                continue
            out.append(n)
        return out

    def strongest(self, scope: str = "", source_id: str = ""):
        rows = self.active_norms(scope, source_id)
        return max(rows, key=lambda x: x.strength) if rows else None

    def summary(self, scope: str = "", source_id: str = "", limit: int = 3) -> str:
        rows = []
        for n in self.norms.values():
            if scope and n.scope != scope:
                continue
            if source_id and n.source_id not in ("", source_id):
                continue
            if n.strength <= 0:
                continue
            rows.append(n)
        rows.sort(key=lambda x: -x.strength)
        return "；".join("规范=%s 强度=%.3f 次数=%d" % (n.id.split(":")[-1], n.strength, n.violations)
                        for n in rows[:max(1, int(limit))])

    def decay(self, now: float = 0.0) -> None:
        now = float(now or time.time())
        changed = False
        for n in self.norms.values():
            if not n.active:
                continue
            age = max(0.0, now - float(n.last_violation or now))
            if n.last_violation:
                n.strength *= math.exp(-age / max(1.0, self.decay_tau))
                changed = True
        if changed:
            self.save()

    def apply_bio(self, mind, appraisal: Appraisal) -> Dict[str, float]:
        a = appraisal.compute()
        aff = mind.affect
        aff.stress = _clip(float(aff.stress) + 0.20 * a.guilt_social + 0.25 * a.guilt_self + 0.10 * a.threat)
        aff.v = max(-1.0, min(1.0, float(aff.v) - 0.25 * a.guilt))
        try:
            aff.dominance = max(-1.0, min(1.0, float(aff.dominance) - 0.10 * a.shame))
        except Exception:
            pass
        try:
            ph = mind.physiology
            ph.cortisol = _clip(float(ph.cortisol) + 0.18 * a.guilt_social + 0.22 * a.guilt_self + 0.06 * a.threat)
            ph.oxytocin = _clip(float(ph.oxytocin) - 0.12 * a.threat)
        except Exception:
            pass
        return {"guilt": round(a.guilt, 4), "guilt_social": round(a.guilt_social, 4),
                "guilt_self": round(a.guilt_self, 4), "shame": round(a.shame, 4),
                "threat": round(a.threat, 4), "stress": round(float(aff.stress), 4)}
