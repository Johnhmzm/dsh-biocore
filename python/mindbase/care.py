"""Care requests: the body asks for help, but can always self-care."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class CareTracker:
    pending: Optional[Dict[str, object]] = None
    offered: int = 0
    accepted: int = 0
    ignored: int = 0
    rejected: int = 0
    last_offer: float = -1e9
    cooldown_s: float = 1800.0
    expiry_s: float = 600.0

    def need(self, body) -> Optional[str]:
        if body.hydration < 0.35:
            return "drink"
        if body.energy < 0.28:
            return "eat"
        if body.fatigue > 0.86:
            return "rest"
        return None

    def offer(self, kind: str, now: Optional[float] = None) -> Dict[str, object]:
        now = float(time.time() if now is None else now)
        self.pending = {"kind": str(kind), "at": now, "expires": now + self.expiry_s}
        self.last_offer = now
        self.offered += 1
        return dict(self.pending)

    def can_offer(self, body, now: Optional[float] = None) -> Optional[str]:
        now = float(time.time() if now is None else now)
        if self.pending is not None:
            return None
        if now - self.last_offer < self.cooldown_s:
            return None
        return self.need(body)

    def respond(self, text: str, body, now: Optional[float] = None) -> Optional[Dict[str, object]]:
        if self.pending is None:
            return None
        now = float(time.time() if now is None else now)
        t = str(text or "")
        reject = any(w in t for w in ("不用", "不要", "不了", "别", "自己来", "不用了"))
        accept = any(w in t for w in ("好", "给你", "喝吧", "吃吧", "去吧", "可以", "行", "谢谢", "嗯"))
        kind = str(self.pending.get("kind") or "rest")
        if reject:
            self.rejected += 1
            self.pending = None
            body.act(kind)
            return {"result": "rejected", "kind": kind, "self_care": True}
        if accept:
            self.accepted += 1
            self.pending = None
            body.act(kind)
            return {"result": "accepted", "kind": kind, "self_care": False}
        return None

    def tick(self, body, now: Optional[float] = None) -> Optional[Dict[str, object]]:
        if self.pending is None:
            return None
        now = float(time.time() if now is None else now)
        if now < float(self.pending.get("expires", now)):
            return None
        kind = str(self.pending.get("kind") or "rest")
        self.pending = None
        self.ignored += 1
        body.act(kind)
        return {"result": "ignored", "kind": kind, "self_care": True}

    def stats(self) -> Dict[str, object]:
        return {"pending": self.pending, "offered": self.offered, "accepted": self.accepted,
                "ignored": self.ignored, "rejected": self.rejected}
