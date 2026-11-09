from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .config import MindConfig
from .engine import MindBase
from .interaction_log import InteractionLogger
from .llm_adapter import LLMAdapter, TemplateLLM
from .types import Event

TRIGGERS = Path(__file__).resolve().parent / "data" / "group_triggers_1v1.jsonl"
LOG = Path(__file__).resolve().parent / "data" / "one_on_one_interactions.jsonl"


class OneOnOneSession:
    """1v1 loop: base decides, expression is optional, every turn is logged."""

    def __init__(self, seed: int = 42, client=None, log_path: Path | str | None = None, metadata: Dict[str, object] | None = None):
        self.mb = MindBase(MindConfig(seed=seed))
        self.adapter = LLMAdapter(self.mb, client or TemplateLLM())
        self.log = InteractionLogger(log_path or LOG)
        self.metadata = dict(metadata or {})

    def step(self, text: str, feedback: float = 0.0, user_response: Optional[str] = None) -> Dict[str, object]:
        before = self.mb.snapshot()
        result = self.adapter.handle(
            Event(text=text, speaker="A", directed=True, ts=self.mb.t + 60, domain="one_on_one"),
            feedback=feedback,
        )
        out = result.outcome
        self.log.record({
            "domain": "one_on_one",
            "event": text,
            "user_response": user_response,
            "state_before": before,
            "action": out.action,
            "reason": out.reason,
            "act": out.act.kind if out.act else None,
            "llm_request": bool(out.llm_request),
            "reply": result.reply,
            "state_after": self.mb.snapshot(),
            **self.metadata,
        })
        return {"reply": result.reply, "action": out.action, "act": out.act.kind if out.act else None, "reason": out.reason}

    def run_triggers(self, limit: int = 5) -> List[Dict[str, object]]:
        rows = [json.loads(x) for x in TRIGGERS.read_text(encoding="utf-8").splitlines() if x.strip()][:limit]
        out = []
        for i, r in enumerate(rows):
            nxt = rows[i + 1]["user_text"] if i + 1 < len(rows) else None
            out.append(self.step(r["user_text"], user_response=nxt))
        return out


if __name__ == "__main__":
    s = OneOnOneSession()
    for r in s.run_triggers(3):
        print(r)
