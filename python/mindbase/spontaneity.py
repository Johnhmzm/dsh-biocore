from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .interaction_log import InteractionLogger
from .llm_adapter import LLMAdapter, TemplateLLM
from .types import Event

LOG = Path(__file__).resolve().parent / "data" / "spontaneous.jsonl"


class SpontaneousLoop:
    """Operational spontaneity: ticks can produce self-initiated speech."""

    def __init__(self, mind, client=None, log_path: Path | str | None = None):
        self.mind = mind
        self.adapter = LLMAdapter(mind, client or TemplateLLM())
        self.log = InteractionLogger(log_path or LOG)

    def tick(self, dt: float = 3600.0) -> Dict[str, object]:
        before = self.mind.snapshot()
        result = self.adapter.handle(Event(kind="tick", ts=self.mind.t + dt), feedback=0.0)
        out = result.outcome
        self.log.record({
            "domain": "spontaneous",
            "event": "tick",
            "dt": dt,
            "state_before": before,
            "action": out.action,
            "reason": out.reason,
            "act": out.act.kind if out.act else None,
            "llm_request": bool(out.llm_request),
            "reply": result.reply,
            "state_after": self.mind.snapshot(),
        })
        return {"t": self.mind.t, "action": out.action, "reason": out.reason, "reply": result.reply}

    def run(self, ticks: int = 24, dt: float = 3600.0) -> List[Dict[str, object]]:
        return [self.tick(dt) for _ in range(ticks)]
