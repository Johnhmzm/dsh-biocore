from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Set

from .types import Appraisal, Event


@dataclass
class AgentModel:
    name: str
    beliefs: Set[str] = field(default_factory=set)
    knowledge: Set[str] = field(default_factory=set)
    desires: Dict[str, float] = field(default_factory=dict)
    intentions: Set[str] = field(default_factory=set)
    # observer thinks target knows these facts
    second_order: Dict[str, Set[str]] = field(default_factory=dict)
    last_seen: float = 0.0

    def snapshot(self) -> Dict[str, object]:
        return {
            "beliefs": list(self.beliefs)[-5:],
            "knowledge": list(self.knowledge)[-5:],
            "desires": {k: round(v, 2) for k, v in list(self.desires.items())[-5:]},
            "intentions": list(self.intentions)[-3:],
            "second_order": {k: list(v)[-3:] for k, v in list(self.second_order.items())[-3:]},
        }


class TheoryOfMind:
    """Lightweight, non-LLM ToM: beliefs, desires, intentions, second-order knowledge.

    This is a heuristic model, not a claim of human-like mindreading.
    """

    INTENTION_CUES = ("想", "要", "打算", "准备", "会去", "plan", "want", "will")
    MENTAL_CUES = ("知道", "以为", "觉得", "认为", "know", "think", "believe")

    def __init__(self):
        self.agents: Dict[str, AgentModel] = {}

    def _agent(self, name: str) -> AgentModel:
        if not name or name == "self":
            name = "self"
        return self.agents.setdefault(name, AgentModel(name=name))

    def observe(self, ev: Event, ap: Appraisal, now: float = 0.0) -> None:
        speaker = ev.speaker or "unknown"
        if speaker == "self":
            return
        fact = (ev.text or "").strip()
        if not fact:
            return
        model = self._agent(speaker)
        model.beliefs.add(fact)
        model.knowledge.add(fact)
        model.desires[fact] = float(ap.valence)
        model.last_seen = now

        lower = fact.lower()
        if any(cue in lower for cue in self.INTENTION_CUES):
            model.intentions.add(fact)

        if any(cue in lower for cue in self.MENTAL_CUES):
            for target in list(self.agents):
                if target != speaker and target in fact:
                    model.second_order.setdefault(target, set()).add(fact)

    def second_order(self, observer: str, target: str, fact: str) -> bool:
        model = self.agents.get(observer)
        return bool(model and fact in model.second_order.get(target, set()))

    def believes(self, speaker: str, fact: str) -> bool:
        model = self.agents.get(speaker)
        return bool(model and fact in model.beliefs)

    def predict_action(self, speaker: str) -> str:
        model = self.agents.get(speaker)
        if model is None:
            return "communicate"
        if model.intentions:
            return "approach"
        if model.desires:
            avg = sum(model.desires.values()) / len(model.desires)
            if avg > 0.20:
                return "approach"
            if avg < -0.20:
                return "avoid"
        return "communicate"

    def context(self) -> Dict[str, object]:
        return {
            "agents": len(self.agents),
            "models": {name: model.snapshot() for name, model in list(self.agents.items())[-4:]},
        }

    def snapshot(self) -> Dict[str, object]:
        return self.context()
