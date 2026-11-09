from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import MindConfig
from .noise import clip


@dataclass
class Goal:
    name: str
    action: str = "speak"  # speak | silence
    priority: float = 0.60
    commitment: float = 0.60
    value: str = "honesty"
    progress: float = 0.0
    created: float = 0.0
    deadline: float = 3600.0
    source: str = "self"


class GoalStack:
    """Long-horizon goals. Top goal is converted into an intention each tick."""

    def __init__(self, cfg: MindConfig):
        self.cfg = cfg
        self.items: List[Goal] = []

    def add(
        self,
        name: str,
        action: str = "speak",
        priority: float = 0.60,
        commitment: float = 0.60,
        value: str = "honesty",
        now: float = 0.0,
        ttl: float = 3600.0,
        source: str = "self",
    ) -> Goal:
        g = Goal(
            name=name,
            action=action,
            priority=clip(priority, 0.0, 1.0),
            commitment=clip(commitment, 0.0, 1.0),
            value=value,
            created=now,
            deadline=now + max(1.0, ttl),
            source=source,
        )
        self.items.append(g)
        return g

    def tick(self, dt: float, now: float) -> None:
        self.items = [g for g in self.items if now < g.deadline and g.progress < 1.0]

    @staticmethod
    def strength(goal: Goal, values=None) -> float:
        base = goal.priority * goal.commitment
        if values is None:
            return base
        return base * (0.5 + 0.5 * values.weight(goal.value))

    def top(self, values=None) -> Optional[Goal]:
        if not self.items:
            return None
        return max(
            self.items,
            key=lambda g: (self.strength(g, values), g.commitment, g.priority, -g.created),
        )

    def conflict(self, values=None) -> Dict[str, object]:
        """Detect the strongest pair of goals that want opposite actions."""
        if len(self.items) < 2:
            return {"level": "none", "score": 0.0, "winner": None, "rival": None}
        ordered = sorted(
            self.items,
            key=lambda g: (self.strength(g, values), g.commitment, -g.created),
            reverse=True,
        )
        top = ordered[0]
        for rival in ordered[1:]:
            if rival.action == top.action:
                continue
            s1 = self.strength(top, values)
            s2 = self.strength(rival, values)
            gap = abs(s1 - s2)
            if gap < 0.10 and min(s1, s2) >= 0.50:
                level = "high"
            elif gap < 0.25:
                level = "medium"
            else:
                level = "low"
            return {
                "level": level,
                "score": round(gap, 4),
                "winner": top.name,
                "rival": rival.name,
                "winner_action": top.action,
                "rival_action": rival.action,
                "winner_value": top.value,
                "rival_value": rival.value,
            }
        return {"level": "none", "score": 0.0, "winner": top.name, "rival": None}

    def resolve(self, values=None) -> tuple:
        return self.top(values), self.conflict(values)

    def sync_intentions(self, intentions, values, now: float) -> Optional[Goal]:
        g = self.top(values)
        if g is None:
            return None
        if any(i.goal == g.name for i in intentions.items):
            return g
        w = values.weight(g.value)
        conflict = self.conflict(values)
        penalty = 0.50 * float(conflict["score"]) if conflict["level"] == "high" else 0.0
        ttl = max(60.0, min(600.0, g.deadline - now))
        intentions.add(
            goal=g.name,
            action=g.action,
            strength=clip(g.priority * w * (1.0 - penalty), 0.05, 1.0),
            commitment=g.commitment,
            now=now,
            ttl=ttl,
            source=g.source,
        )
        return g

    def on_action(self, action: str, now: float, values=None):
        g = self.top(values)
        if g is None:
            return None
        if action == g.action:
            g.progress = clip(g.progress + 0.10, 0.0, 1.0)
        if g.progress >= 1.0:
            self.items.remove(g)
            return g
        return None

    def snapshot(self) -> List[Dict[str, object]]:
        return [
            {
                "name": g.name,
                "action": g.action,
                "priority": round(g.priority, 3),
                "commitment": round(g.commitment, 3),
                "value": g.value,
                "progress": round(g.progress, 3),
                "deadline": round(g.deadline, 1),
            }
            for g in self.items
        ]
