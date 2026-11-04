from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .actions import Action


@dataclass
class Event:
    """One input to the base. Text is optional; callers may pre-compute affect."""

    text: str = ""
    speaker: str = "user"
    kind: str = "message"  # message | tick | self_action | observation
    directed: bool = False  # @ / name called
    valence: Optional[float] = None  # [-1, 1] if caller already knows it
    arousal: Optional[float] = None  # [0, 1]
    relevance: Optional[float] = None  # [0, 1]
    social: Optional[float] = None  # [-1, 1], social signal
    control: Optional[float] = None  # [0, 1]
    ts: Optional[float] = None  # absolute sim time; if None, cfg.dt is used
    domain: str = "chat"  # chat | embodied | ...


@dataclass
class Appraisal:
    novelty: float
    relevance: float
    control: float
    rpe: float
    social: float
    threat: float
    valence: float
    arousal: float
    # OCC-style appraisal dimensions (appended for backward compatibility)
    goal_relevance: float = 0.0
    goal_congruence: float = 0.0
    agency: float = 0.0  # -1 other, +1 self
    certainty: float = 0.5
    coping_potential: float = 0.5
    norm_compliance: float = 0.5
    occ_emotion: str = ""


@dataclass
class Feedback:
    """External or self-generated feedback on a reply.

    source: user | llm | self
    confidence: 0..1, how much the base should trust this signal.
    """
    valence: float
    source: str = "user"
    confidence: float = 1.0
    text: str = ""


@dataclass
class LLMRequest:
    """The base's decision to call the LLM. None means stay silent."""
    mode: str = "reply"  # reply | proactive | clarify | inner | tool
    urgency: float = 0.5
    max_tokens: int = 300
    reason: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    response_format: Optional[Dict[str, Any]] = None


@dataclass
class Outcome:
    t: float
    action: str  # speak | silence | ask
    reason: str
    prompt_state: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    llm_request: Optional[LLMRequest] = None
    act: Optional[Action] = None
