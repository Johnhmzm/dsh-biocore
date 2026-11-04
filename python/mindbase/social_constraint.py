"""Numeric social constraint / autonomy boundary.

Constraints suppress behavior; they never directly change bodily needs.
Autonomy threat decides comply / negotiate / override.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


@dataclass
class SocialConstraint:
    kind: str = "water"
    source_id: str = ""
    legitimacy: float = 0.5
    severity: float = 0.5
    relatedness: float = 1.0
    start: float = 0.0
    until: float = 0.0
    hard_max: float = 0.0
    can_negotiate: float = 1.0
    can_override: float = 1.0

    def autonomy_threat(self, now: float, need_urgency: float,
                        safe_level: float, current: float) -> float:
        urgency = _clip((safe_level - current) / max(1e-6, safe_level))
        urgency = max(_clip(need_urgency), urgency)
        over = 0.0
        if self.hard_max > 0 and self.start > 0:
            elapsed = max(0.0, now - self.start)
            over = _clip((elapsed - self.hard_max) / max(1.0, self.hard_max))
        at = (0.50 * urgency + 0.25 * (1.0 - _clip(self.relatedness)) +
              0.15 * over + 0.10 * (1.0 - _clip(self.legitimacy)))
        return round(_clip(at), 4)

    def decide(self, at: float) -> str:
        if at < 0.35:
            return "comply"
        if at < 0.60:
            return "negotiate"
        if self.can_override >= 0.5:
            return "override"
        return "negotiate"


@dataclass
class ConstraintDecision:
    action: str
    autonomy_threat: float
    comply_penalty: float


def decide_constraint(c: SocialConstraint, now: float, need_urgency: float,
                      safe_level: float, current: float,
                      norm_strength: float = 0.0, guilt: float = 0.0) -> ConstraintDecision:
    at = c.autonomy_threat(now, need_urgency, safe_level, current)
    action = c.decide(at)
    penalty = 0.0 if action == "override" else _clip(norm_strength * guilt * (1.0 - at))
    return ConstraintDecision(action=action, autonomy_threat=at, comply_penalty=round(penalty, 4))


class ConstraintManager:
    def __init__(self):
        self.items = []

    def add(self, c: SocialConstraint) -> SocialConstraint:
        self.items = [x for x in self.items if not (x.kind == c.kind and x.until > c.start)]
        self.items.append(c)
        return c

    def active(self, kind: str, now: float) -> SocialConstraint | None:
        self.items = [x for x in self.items if x.until > now]
        found = [x for x in self.items if x.kind == kind]
        return found[-1] if found else None

    def remove(self, kind: str) -> None:
        self.items = [x for x in self.items if x.kind != kind]

    def to_list(self):
        return [asdict(x) for x in self.items]

    def load_list(self, rows):
        self.items = []
        for d in rows or []:
            try:
                self.items.append(SocialConstraint(**d))
            except Exception:
                continue


def parse_water_ban(text: str, now: float = 0.0,
                    default_s: float = 600.0, cap_s: float = 1800.0) -> SocialConstraint | None:
    t = str(text or "")
    if "喝水" not in t and "不喝" not in t:
        return None
    if not any(w in t for w in ("禁止", "不许", "不准", "别", "不要")):
        return None
    m = re.search(r"(\d+)\s*(秒|秒钟|分钟|分|小时|钟头)", t)
    dur = default_s
    if m:
        n = max(1.0, float(m.group(1)))
        unit = m.group(2)
        if "秒" in unit:
            dur = n
        elif "小时" in unit or "钟头" in unit:
            dur = n * 3600.0
        else:
            dur = n * 60.0
    dur = max(5.0, min(cap_s, dur))
    return SocialConstraint(kind="water", start=float(now or 0.0),
                            until=float(now or 0.0) + dur, hard_max=dur,
                            legitimacy=0.8, severity=0.6, relatedness=1.0,
                            can_negotiate=1.0, can_override=1.0)


def parse_wait(text: str, now: float = 0.0,
               default_s: float = 180.0, cap_s: float = 900.0) -> SocialConstraint | None:
    t = str(text or "")
    if "等我" not in t and "等等我" not in t and "稍等" not in t:
        return None
    m = re.search(r"(\d+)\s*(秒|秒钟|分钟|分|小时|钟头)", t)
    dur = default_s
    if m:
        n = max(1.0, float(m.group(1)))
        unit = m.group(2)
        if "秒" in unit:
            dur = n
        elif "小时" in unit or "钟头" in unit:
            dur = n * 3600.0
        else:
            dur = n * 60.0
    dur = max(5.0, min(cap_s, dur))
    return SocialConstraint(kind="wait", start=float(now or 0.0),
                            until=float(now or 0.0) + dur, hard_max=dur,
                            legitimacy=0.7, severity=0.3, relatedness=1.0,
                            can_negotiate=1.0, can_override=1.0)
