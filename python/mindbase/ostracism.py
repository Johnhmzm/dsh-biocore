"""Track outgoing messages that get no reply (ordinary chat ostracism)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class ReplyTracker:
    timeout_s: float = 300.0
    awaiting: Optional[Dict[str, object]] = None
    ignored: int = 0
    replied: int = 0
    last_ignored: Optional[Dict[str, object]] = None

    def mark_sent(self, kind: str, message: str, now: float) -> None:
        self.awaiting = {"kind": str(kind), "message": str(message)[:400], "at": float(now)}

    def mark_replied(self) -> None:
        if self.awaiting is not None:
            self.replied += 1
        self.awaiting = None

    def check(self, now: float, timeout_s: Optional[float] = None) -> Optional[Dict[str, object]]:
        if self.awaiting is None:
            return None
        limit = float(timeout_s if timeout_s is not None else self.timeout_s)
        if float(now) - float(self.awaiting.get("at", now)) < limit:
            return None
        event = dict(self.awaiting)
        event["elapsed"] = round(float(now) - float(event.get("at", now)), 1)
        self.awaiting = None
        self.ignored += 1
        self.last_ignored = event
        return event
