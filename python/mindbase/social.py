from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict

from .config import MindConfig
from .noise import clip, ou
from .relationships import update_stage
from .types import Appraisal, Event


@dataclass
class Bond:
    attachment: float = 0.10
    trust: float = 0.50
    familiarity: float = 0.10
    responsiveness: float = 0.50
    valence: float = 0.0
    last_seen: float = 0.0
    stage: str = "stranger"


class Social:
    """Per-speaker bonds + a slow group-valence state (contagion)."""

    DAY = 86400.0
    GRACE = 1.0 * DAY
    TAU_FAMILIARITY = 30.0 * DAY
    TAU_TRUST = 60.0 * DAY
    TAU_ATTACHMENT = 90.0 * DAY

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.bonds: Dict[str, Bond] = {}
        self.group_valence = 0.0
        self.trust_gain = 1.0

    def on_event(self, ev: Event, ap: Appraisal, now: float, dt: float) -> None:
        if ev.kind == "message" and ev.speaker and ev.speaker != "self":
            b = self.bonds.setdefault(ev.speaker, Bond())
            b.familiarity = clip(b.familiarity + 0.010, 0.0, 1.0)
            b.trust = clip(b.trust + 0.020 * ap.social * float(getattr(self, "trust_gain", 1.0)), 0.0, 1.0)
            b.attachment = clip(b.attachment + 0.005 * max(0.0, ap.valence), 0.0, 1.0)
            b.valence = ou(b.valence, ap.valence, 1.0 / 300.0, 0.05, dt, self.rng)
            b.last_seen = now
            update_stage(b)
        self.group_valence = ou(
            self.group_valence,
            ap.valence,
            1.0 / max(self.cfg.social_tau, 1e-6),
            0.05,
            dt,
            self.rng,
        )
        self.group_valence = clip(self.group_valence, -1.0, 1.0)

    def on_feedback(
        self,
        speaker: str,
        feedback: float | None,
        responded: bool = True,
        now: float = 0.0,
        dt: float = 60.0,
        repair_factor: float = 1.0,
    ) -> None:
        if not speaker or speaker == "self":
            return
        b = self.bonds.setdefault(speaker, Bond())
        if responded:
            fb = clip(feedback if feedback is not None else 0.0, -1.0, 1.0)
            mult = 1.6 if fb < 0 else 1.0
            gain = 0.04 * fb * mult * (float(repair_factor) if fb > 0 else 1.0) * float(getattr(self, "trust_gain", 1.0))
            b.trust = clip(b.trust + gain, 0.0, 1.0)
            b.responsiveness = clip(b.responsiveness + (0.02 * fb), 0.0, 1.0)
            b.attachment = clip(b.attachment + 0.03 * max(0.0, fb), 0.0, 1.0)
            b.familiarity = clip(b.familiarity + (0.005 if fb >= 0 else -0.012), 0.0, 1.0)
            target = fb
        else:
            b.trust = clip(b.trust - 0.03, 0.0, 1.0)
            b.attachment = clip(b.attachment - 0.02, 0.0, 1.0)
            target = -0.40
        b.valence = ou(b.valence, target, 1.0 / 300.0, 0.05, dt, self.rng)
        b.last_seen = now
        update_stage(b)
        self.group_valence = clip(0.80 * self.group_valence + 0.20 * target, -1.0, 1.0)

    def tick(self, dt: float, now: float) -> None:
        """Slow relationship decay when a bond has been idle beyond the grace period."""
        if dt <= 0:
            return
        for b in self.bonds.values():
            idle = max(0.0, now - b.last_seen)
            if idle < self.GRACE:
                continue
            b.familiarity = clip(b.familiarity * math.exp(-dt / self.TAU_FAMILIARITY), 0.0, 1.0)
            b.trust = clip(b.trust * math.exp(-dt / self.TAU_TRUST), 0.0, 1.0)
            b.attachment = clip(b.attachment * math.exp(-dt / self.TAU_ATTACHMENT), 0.0, 1.0)
            update_stage(b)

    def repair(self, speaker: str, effort: float = 1.0, now: float = 0.0) -> Bond:
        """Active repair: apology / care raises trust and attachment."""
        if not speaker or speaker == "self":
            raise ValueError("repair needs a real speaker")
        e = clip(float(effort), 0.0, 1.0)
        b = self.bonds.setdefault(speaker, Bond())
        b.trust = clip(b.trust + 0.06 * e, 0.0, 1.0)
        b.attachment = clip(b.attachment + 0.05 * e, 0.0, 1.0)
        b.familiarity = clip(b.familiarity + 0.01 * e, 0.0, 1.0)
        b.valence = clip(b.valence + 0.20 * e, -1.0, 1.0)
        b.last_seen = now
        update_stage(b)
        self.group_valence = clip(self.group_valence + 0.10 * e, -1.0, 1.0)
        return b

    def trust_for(self, speaker: str) -> float:
        b = self.bonds.get(speaker)
        return float(b.trust) if b else 0.0

    def stage(self, speaker: str) -> str:
        b = self.bonds.get(speaker)
        return b.stage if b else "stranger"

    def snapshot(self) -> Dict[str, object]:
        return {
            "group_valence": round(self.group_valence, 4),
            "bonds": {
                k: {
                    "attachment": round(v.attachment, 3),
                    "trust": round(v.trust, 3),
                    "familiarity": round(v.familiarity, 3),
                    "stage": v.stage,
                }
                for k, v in self.bonds.items()
            },
        }
