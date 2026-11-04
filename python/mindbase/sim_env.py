"""Simulated chat environment for offline evolution.

Wraps MindBase + Kernel and a Teacher.  No QQ, no network, no human in the loop.
Each episode is a sequence of user turns; the teacher provides the turn and the
outcome reward.  The base's own decision modules remain in charge of actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .types import Event
from .teacher import Scenario, Teacher


@dataclass
class EpisodeResult:
    seed: int
    scenario: Scenario
    turns: int = 0
    reward_sum: float = 0.0
    rewards: List[float] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    states: List[Dict[str, float]] = field(default_factory=list)
    noise_metrics: Dict[str, float] = field(default_factory=dict)

    @property
    def mean_reward(self) -> float:
        return self.reward_sum / self.turns if self.turns else 0.0


class SimEnv:
    """Minimal simulated environment: user turns -> MindBase action -> reward."""

    def __init__(self, mind, kernel, teacher: Teacher, user: str = "user", dt: float = 60.0, noise_system=None):
        self.mind = mind
        self.kernel = kernel
        self.teacher = teacher
        self.user = str(user)
        self.dt = float(dt)
        self.noise_system = noise_system if noise_system is not None else getattr(mind, "endogenous_noise", None)

    def run_episode(self, seed: int, max_turns: Optional[int] = None) -> EpisodeResult:
        scenario = self.teacher.scenario(int(seed))
        turns = list(scenario.turns)
        if max_turns is not None:
            turns = turns[:max(0, int(max_turns))]
        result = EpisodeResult(seed=int(seed), scenario=scenario)
        for turn in turns:
            if self.noise_system is not None:
                try:
                    self.noise_system.step(self.dt)
                    self.noise_system.inject(self.mind)
                except Exception:
                    pass
            event = Event(text=str(turn.text), speaker=self.user,
                          directed=bool(turn.directed), ts=self.mind.t + self.dt,
                          domain="chat")
            try:
                self.kernel.observe(str(turn.text), self.user)
            except Exception:
                pass
            action = self.kernel.step(event)
            kind = str(getattr(action, "kind", "silence"))
            reward = self.teacher.feedback(scenario, turn, kind, self.mind)
            self.kernel.feedback(str(turn.text), "", float(reward), self.user)
            result.turns += 1
            result.reward_sum += float(reward)
            result.rewards.append(float(reward))
            result.actions.append(kind)
            try:
                result.states.append({
                    "v": float(getattr(self.mind.affect, "v", 0.0)),
                    "stress": float(getattr(self.mind.affect, "stress", 0.0)),
                    "energy": float(self.mind.drives.values.get("energy", 0.0)),
                    "social": float(self.mind.drives.values.get("social", 0.0)),
                    "viability": float(self.mind.viability_state.get("viability", 1.0)),
                })
            except Exception:
                pass
        if self.noise_system is not None:
            try:
                result.noise_metrics = dict(self.noise_system.metrics())
            except Exception:
                pass
        return result
