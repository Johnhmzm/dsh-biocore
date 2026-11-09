from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol, Sequence

from .types import Event, LLMRequest, Outcome


class LLMClient(Protocol):
    """Anything that can turn an LLMRequest into text."""

    def generate(self, request: LLMRequest) -> str:
        ...


@dataclass
class TurnResult:
    outcome: Outcome
    request: Optional[LLMRequest]
    reply: Optional[str]
    post: Optional[Dict[str, object]]
    metrics: Optional[Dict[str, float]] = None


class ScriptedLLM:
    """Deterministic test client with a queue of replies."""

    def __init__(self, replies: Optional[Sequence[str]] = None, default: str = "……"):
        self.replies: Deque[str] = deque(replies or [])
        self.default = default
        self.calls: List[LLMRequest] = []

    def generate(self, request: LLMRequest) -> str:
        self.calls.append(request)
        if self.replies:
            return self.replies.popleft()
        return self.default


class TemplateLLM:
    """No-model fallback: verbalizes a few context fields. Replace with a real client."""

    def __init__(self) -> None:
        self.calls: List[LLMRequest] = []

    def generate(self, request: LLMRequest) -> str:
        self.calls.append(request)
        ctx = request.context
        mood = ctx.get("mood", 0.0)
        identity = ctx.get("identity", 0.0)
        rel = ctx.get("relationship", "unknown")
        style = ctx.get("expression_style", "balanced")
        return f"[{request.mode}/{style}] mood={mood} identity={identity} rel={rel}"


def build_prompt(request: LLMRequest, character_prompt: Optional[str] = None) -> Dict[str, object]:
    """Render the request into a natural chat prompt.

    The state stays data; the client only turns the last user utterance into a reply.
    """
    ctx = request.context
    state = {
        "mood": ctx.get("mood"),
        "v": ctx.get("v"),
        "a": ctx.get("a"),
        "stress": ctx.get("stress"),
        "identity": ctx.get("identity"),
        "continuity": ctx.get("continuity"),
        "relationship": ctx.get("relationship"),
        "expression": ctx.get("expression_style"),
        "style_hint": ctx.get("relationship_guidance"),
        "goal": ctx.get("goal"),
        "narrative": ctx.get("narrative"),
        "constructs": ctx.get("constructs"),
        "social_need": ctx.get("social_need"),
        "anticipation": ctx.get("anticipation"),
        "proactive_emotion": ctx.get("proactive_emotion"),
        "unfinished": ctx.get("unfinished"),
        "emotion_state": ctx.get("emotion_state"),
    }
    last_user = ctx.get("last_user") or ""
    style = (
        "语气参考（只学亲近、口语、有来有回，不要照抄）：\n"
        "哟，这话我爱听~我也爱你啊老板，晚上别放我鸽子，说好了来的。\n"
        "行啊，那就这么说定了，你可别又临时变卦。\n"
    )
    base = (
        style
        + "你是这个数字生命体的表达层。先接住用户的情绪，再回应；不要先讲道理或给建议。只输出对用户说的那一句话。"
        "不要解释规则，不要复述上下文，不要输出思考过程，不要提到 JSON。"
        "用中文，自然、简短。当前内部状态（只供你把握语气，不要说出来）："
        + json.dumps(state, ensure_ascii=False, default=str)
    )
    if request.mode == "semantic":
        return {
            "system": ctx.get("system", ""),
            "user": ctx.get("user", ""),
            "mode": "semantic",
            "max_tokens": request.max_tokens,
            "context": ctx,
        }
    if request.mode == "proactive":
        system = (
            "你是这个数字生命体的表达层。现在没有新的用户消息，是你自己想主动说一句。"
            "不要问用户“你想让我回复什么”，不要等指令；直接分享一个念头、关心、或发起一个具体话题。"
            "用中文，一到两句，自然。根据当前情绪调整语气：不安时低调克制，开心时活泼，不要黏人。"
            "不要重复最近主动说过的话题和句式。当前内部状态（只影响语气，不要说出来）："
            + json.dumps(state, ensure_ascii=False, default=str)
        )
        if character_prompt:
            system = character_prompt.strip() + "\n\n---\n" + system
        cue = ctx.get("proactive_cue")
        if cue:
            system = system + "\n你此刻的念头（只用来决定说什么，不要复述原句）：" + str(cue)
        recent = ctx.get("recent") or []
        if recent:
            system = system + "\n最近对话（按时间顺序，只用于衔接，不要复述）：\n" + "\n".join(str(x) for x in recent[-8:])
        recent_proactive = ctx.get("recent_proactive") or []
        if recent_proactive:
            system = system + "\n最近已经主动说过（不要重复主题/句式）：" + " / ".join(str(x)[:30] for x in recent_proactive[-3:])
        system = system + "\n只依据最近对话和你自己的内部状态，不要声称看到了对话里没有的信息。"
        user = "现在请主动对用户说一句话，换一个和最近不同的话题或角度。"
    else:
        if character_prompt:
            system = (
                character_prompt.strip()
                + "\n\n---\n"
                + "此刻的内部状态（只影响语气，不要说出来，也不要解释它）："
                + json.dumps(state, ensure_ascii=False, default=str)
                + "\n只输出对用户说的那一句话，不要写旁白、不要解释设定。"
            )
        else:
            system = base
        recent = ctx.get("recent") or []
        if recent:
            history = "\n".join(str(x) for x in recent[-6:])
            system = system + "\n\n最近对话（按时间顺序，只用于衔接，不要复述）：\n" + history
        user = f"用户刚刚说：{last_user}\n\n请直接回复这句话。"
    return {
        "system": system,
        "user": user,
        "mode": request.mode,
        "max_tokens": request.max_tokens,
        "context": request.context,
    }


@dataclass
class ExpressionMetrics:
    length: int
    question: float
    diversity: float
    novelty: float
    confidence: float

    def to_dict(self) -> Dict[str, float]:
        return {"length": self.length, "question": self.question, "diversity": round(self.diversity, 4), "novelty": round(self.novelty, 4), "confidence": round(self.confidence, 4)}


def _bigrams(text: str):
    t = "".join(str(text).split())
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def expression_metrics(reply: str, request: LLMRequest) -> ExpressionMetrics:
    text = str(reply or "")
    length = len(text)
    question = 1.0 if ("?" in text or "？" in text) else 0.0
    chars = [c for c in text if not c.isspace()]
    diversity = (len(set(chars)) / max(1, len(chars))) if chars else 0.0
    recent = " ".join(str(x) for x in (request.context.get("recent") or [])[-4:])
    a, b = _bigrams(text), _bigrams(recent)
    novelty = 1.0 - (len(a & b) / max(1, len(a | b))) if a and b else 1.0
    confidence = max(0.0, min(1.0, 0.35 + 0.25 * diversity + 0.30 * novelty + 0.10 * question))
    return ExpressionMetrics(length=length, question=question, diversity=diversity, novelty=novelty, confidence=confidence)


class LLMAdapter:
    """Bridge: base decides -> client generates -> base.post_reply learns.

    The adapter is stateless with respect to the mind; it never owns emotion,
    memory, or the decision to speak.
    """

    def __init__(self, mind, client: Optional[LLMClient] = None):
        self.mind = mind
        self.client = client or TemplateLLM()
        self.history: List[TurnResult] = []

    def handle(
        self,
        event: Event,
        feedback: Optional[float] = None,
        speaker: Optional[str] = None,
        responded: bool = True,
        feedback_source: str = "user",
        feedback_fn: Optional[Callable[[Event, str], Optional[float]]] = None,
    ) -> TurnResult:
        outcome = self.mind.step(event)
        if outcome.llm_request is None:
            result = TurnResult(outcome=outcome, request=None, reply=None, post=None)
        else:
            reply = self.client.generate(outcome.llm_request)
            metrics = expression_metrics(reply, outcome.llm_request)
            try:
                self.mind.self_model.on_expression(metrics.to_dict())
            except Exception:
                pass
            if feedback is None and feedback_fn is not None:
                feedback = feedback_fn(event, reply)
            post = self.mind.post_reply(
                reply,
                feedback=feedback,
                speaker=speaker or event.speaker,
                responded=responded,
                feedback_source=feedback_source,
            )
            result = TurnResult(outcome=outcome, request=outcome.llm_request, reply=reply, post=post, metrics=metrics.to_dict())
        self.history.append(result)
        return result

    def run(
        self,
        events: Sequence[Event],
        feedbacks: Optional[Sequence[Optional[float]]] = None,
    ) -> List[TurnResult]:
        results = []
        for i, event in enumerate(events):
            fb = feedbacks[i] if feedbacks is not None and i < len(feedbacks) else None
            results.append(self.handle(event, feedback=fb))
        return results
