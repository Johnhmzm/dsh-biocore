from __future__ import annotations

from typing import Dict, List

from .goal_generator import ActionCritic, GoalGenerator
from .grid_world import GridWorld
from .insight import InsightEngine
from .types import Event
from .noise import jaccard


class SelfWilledInsight:
    """Insight as self-will + iteration: self-generated goal, candidates, selection, feedback."""

    def __init__(self, mind, env=None):
        self.mind = mind
        self.engine = InsightEngine(mind.memory)
        self.goals = GoalGenerator(getattr(mind.cfg, "seed", 1))
        self.actions = ActionCritic(getattr(mind.cfg, "seed", 1))
        self.env = env or GridWorld(seed=getattr(mind.cfg, "seed", 1))

    def self_goal(self) -> str:
        criticality = float(getattr(getattr(self.mind, "critical", None), "metastability", 0.0))
        return str(self.goals.choose(self.mind.drives, criticality)["goal"])

    def iterate(self, steps: int = 5, now: float | None = None) -> Dict[str, object]:
        now = self.mind.t if now is None else float(now)
        goal = self.self_goal()
        rows: List[Dict[str, object]] = []
        goal_chars = set(goal)
        for _ in range(steps):
            criticality = float(getattr(getattr(self.mind, "critical", None), "metastability", 0.0))
            cands = self.engine.generate_many(goal, now, k=6, criticality=criticality)
            if not cands:
                break
            selected = max(cands, key=lambda c: c["score"] + 0.5 * jaccard(set(c["candidate"]), goal_chars))
            action_features = self.goals.features(self.mind.drives, criticality)
            kind = self.actions.choose(action_features)
            potential_before = self.env.potential()
            reward = self.env.act(kind)
            potential_after = self.env.potential()
            shaping = 0.90 * potential_after - potential_before
            self.mind.drives.values["energy"] = self.env.energy
            # dense outcome = environment reward + potential-based shaping + curiosity
            outcome = float(reward) + shaping + 0.01 * float(selected.get("novelty", 0.0))
            rows.append({"goal": goal, "selected": selected["candidate"], "score": selected["score"], "action": kind, "outcome": round(outcome, 4), "shaping": round(shaping, 4), "energy": round(self.env.energy, 3)})
            # feedback: selected recombination becomes material; goal policy learns from action outcome
            self.mind.memory.update_fact(selected["candidate"], now)
            self.goals.learn(int(self.goals.choose(self.mind.drives, criticality)["index"]), outcome, self.goals.features(self.mind.drives, criticality))
            self.actions.learn(kind, outcome, action_features)
        return {"goal": goal, "iterations": len(rows), "rows": rows}
