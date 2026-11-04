from __future__ import annotations

from typing import Any, Dict, Optional

from .config import MindConfig
from .noise import clip
from .expression import policy_for
from .relationships import guidance
from .types import LLMRequest


class LLMGate:
    """The base owns the decision to call the LLM.

    Digital-fly framing: the whole-brain/action layer generates behaviour;
    the LLM is a downstream vocal organ, not the controller.
    """

    def __init__(self, cfg: MindConfig):
        self.cfg = cfg

    def build(
        self,
        mode: str,
        state: Any,
        salience: float,
        appraisal: Any,
        intention: Optional[Any] = None,
        reason: str = "",
    ) -> LLMRequest:
        urgency = clip(
            0.40 * state.affect.a + 0.30 * state.affect.stress + 0.30 * salience,
            0.0,
            1.0,
        )
        speaker = getattr(state, "last_speaker", None)
        relationship = state.social.stage(speaker) if speaker and hasattr(state, "social") else "unknown"
        stage_meta = guidance(relationship)
        if mode == "proactive" and relationship in ("stranger", "acquaintance"):
            mode = "reply"  # strangers do not get proactive initiations
        expression = policy_for(state.persona, int(stage_meta["max_tokens"]))
        max_tokens = expression.max_tokens
        top_goal = state.goals.top(state.values)
        conflict = state.goals.conflict(state.values)
        recent = [s["item"] for s in state.wm.snapshot()[:3]]
        context: Dict[str, Any] = {
            "mood": round(state.affect.mood, 2),
            "v": round(state.affect.v, 2),
            "a": round(state.affect.a, 2),
            "stress": round(state.affect.stress, 2),
            "drives": state.drives.snapshot(),
            "focus": state.attention.focus,
            "confidence": round(state.meta.confidence, 2),
            "identity": round(state.self_model.identity, 2),
            "continuity": round(state.self_model.continuity, 2),
            "agency": round(state.self_model.agency, 2),
            "persona": {k: round(v, 3) for k, v in state.persona.numbers().items()},
            "narrative": state.narrative.context(),
            "constructs": dict(state.persona.constructs),
            "expression": dict(state.persona.extra),
            "expression_style": expression.style,
            "expression_guidance": expression.guidance,
            "directness": expression.directness,
            "disclosure": expression.disclosure,
            "guard": expression.guard,
            "willpower": round(state.will.willpower, 2),
            "intention": intention.goal if intention else None,
            "goal": top_goal.name if top_goal else None,
            "goal_progress": round(top_goal.progress, 3) if top_goal else None,
            "goal_priority": round(top_goal.priority, 3) if top_goal else None,
            "goal_action": top_goal.action if top_goal else None,
            "goal_conflict": conflict,
            "salience": round(salience, 2),
            "last_user": getattr(state, "last_user_text", None),
            "relationship": relationship,
            "relationship_guidance": stage_meta["style"],
            "recent": recent,
        }
        return LLMRequest(
            mode=mode,
            urgency=round(urgency, 3),
            max_tokens=max_tokens,
            reason=reason,
            context=context,
        )
