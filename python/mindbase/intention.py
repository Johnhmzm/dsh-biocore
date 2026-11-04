from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import MindConfig
from .noise import clip


@dataclass
class Intention:
    """A commitment to do (or not do) something, with a horizon."""

    goal: str
    action: str = "speak"  # speak | silence
    strength: float = 0.60
    commitment: float = 0.60
    created: float = 0.0
    expiry: float = 3600.0
    source: str = "self"


class IntentionStack:
    """Small stack of active intentions. Top = max(strength * commitment)."""

    def __init__(self, cfg: MindConfig):
        self.cfg = cfg
        self.items: List[Intention] = []

    def add(
        self,
        goal: str,
        action: str = "speak",
        strength: float = 0.60,
        commitment: float = 0.60,
        now: float = 0.0,
        ttl: float = 3600.0,
        source: str = "self",
    ) -> Intention:
        it = Intention(
            goal=goal,
            action=action,
            strength=clip(strength, 0.0, 1.0),
            commitment=clip(commitment, 0.0, 1.0),
            created=now,
            expiry=now + max(1.0, ttl),
            source=source,
        )
        self.items.append(it)
        return it

    def tick(self, dt: float, now: float) -> None:
        for it in self.items:
            it.strength = clip(it.strength * math.exp(-dt / 3600.0), 0.0, 1.0)
        self.items = [i for i in self.items if now < i.expiry and i.strength > 0.05]

    def top(self) -> Optional[Intention]:
        if not self.items:
            return None
        return max(self.items, key=lambda i: i.strength * i.commitment)

    def complete(self, goal: str) -> None:
        self.items = [i for i in self.items if i.goal != goal]

    def snapshot(self) -> List[Dict[str, object]]:
        return [
            {
                "goal": i.goal,
                "action": i.action,
                "strength": round(i.strength, 3),
                "commitment": round(i.commitment, 3),
                "expiry": round(i.expiry, 1),
            }
            for i in self.items
        ]
