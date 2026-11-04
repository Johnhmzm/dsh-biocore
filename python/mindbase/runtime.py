"""Runtime kernel: connects MindBase to experiments and turn-level reward."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .actions import Action, kind_for_style
from .noise import clip
from .types import Event, Outcome


@dataclass
class KernelOutcome:
    t: float = 0.0
    action: str = "silence"
    reason: str = ""
    state: Dict[str, object] = field(default_factory=dict)
    act: Optional[Action] = None
    viability: float = 1.0
    reward: float = 0.0
    raw_reward: float = 0.0


class Kernel:
    """Small event bus around MindBase."""

    def __init__(self, mind, experiments=None):
        self.mind = mind
        self.experiments: List[object] = list(experiments or [])
        self.last_text: str = ""
        self.last_speaker: Optional[str] = None
        self.last_outcome: Optional[Outcome] = None
        self.last_feedback: Optional[KernelOutcome] = None

    def _notify(self, name: str, *args) -> None:
        for exp in self.experiments:
            fn = getattr(exp, name, None)
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception:
                pass

    def observe(self, text: str, user: Optional[str] = None, speaker: Optional[str] = None) -> None:
        self.last_text = str(text or "")
        who = speaker if speaker is not None else user
        if who:
            self.last_speaker = str(who)
            try:
                self.mind.last_speaker = str(who)
            except Exception:
                pass
        if self.last_text:
            try:
                self.mind.last_user_text = self.last_text
            except Exception:
                pass

    def step(self, event: Event):
        out = self.mind.step(event)
        self.last_outcome = out
        act = out.act or Action(kind=kind_for_style(out.action), style=str(out.action))
        state = dict(out.state or {})
        try:
            self._notify("on_state", state)
            self._notify("on_action", act)
        except Exception:
            pass
        return act

    def feedback(self, text: str, reply: str, reward: float = 0.0,
                 speaker: Optional[str] = None):
        viability = float((getattr(self.mind, "viability_state", {}) or {}).get("viability", 1.0))
        try:
            intrinsic = float(self.mind.drives.intrinsic_reward())
        except Exception:
            intrinsic = 0.0
        try:
            pred_err = float(getattr(self.mind.self_model, "prediction_error", 0.0))
        except Exception:
            pred_err = 0.0
        try:
            stress = float(self.mind.affect.stress)
        except Exception:
            stress = 0.0
        turn_reward = clip(0.60 * float(reward or 0.0)
                           + 0.20 * (viability - 0.5)
                           + 0.12 * pred_err
                           + 0.08 * stress,
                           -1.0, 1.0)
        ko = KernelOutcome(t=float(getattr(self.mind, "t", 0.0)), action="communicate",
                           state=dict(getattr(self.mind, "viability_state", {}) or {}),
                           viability=viability, reward=round(turn_reward, 6),
                           raw_reward=float(reward or 0.0))
        self.last_feedback = ko
        try:
            self._notify("on_turn", str(text or ""), str(reply or ""))
            self._notify("on_outcome", ko)
        except Exception:
            pass
        return ko

    def tick(self, dt: Optional[float] = None):
        out = self.mind.tick(dt)
        self.last_outcome = out
        try:
            act = out.act or Action(kind=kind_for_style(out.action), style=str(out.action))
            self._notify("on_state", dict(out.state or {}))
            self._notify("on_action", act)
        except Exception:
            pass
        return out
