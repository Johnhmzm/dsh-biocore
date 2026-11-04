"""Per-group initiative quota with internal feedback.

A quota is an external limit, but the feeling of being blocked is internal:
urge, frustration, expected_success, and a learned hold after repeated blocks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


class GroupQuota:
    def __init__(self, limit_per_hour: int = 5, path: Path | str | None = None,
                 learning_rate: float = 0.12):
        self.limit = max(0, int(limit_per_hour))
        self.path = Path(path) if path else None
        self.lr = float(learning_rate)
        self.sent: list[float] = []
        self.blocked: list[float] = []
        self.urge = 0.0
        self.frustration = 0.0
        self.expected_success = 0.6
        self.blocked_count = 0
        self.attempts = 0
        self.held_count = 0
        self.forced_count = 0
        self.last_block = 0.0
        self.last_send = 0.0
        self.load()

    def _prune(self, now: float) -> None:
        self.sent = [t for t in self.sent if now - float(t) < 3600.0]
        self.blocked = [t for t in self.blocked if now - float(t) < 3600.0]

    def can_send(self, now: float | None = None) -> bool:
        now = float(now if now is not None else time.time())
        self._prune(now)
        if self.limit <= 0:
            return True
        return len(self.sent) < self.limit

    def record_send(self, now: float | None = None) -> None:
        now = float(now if now is not None else time.time())
        self._prune(now)
        self.sent.append(now)
        self.attempts += 1
        self.expected_success = _clip(self.expected_success + self.lr * (1.0 - self.expected_success), 0.0, 1.0)
        self.urge = _clip(self.urge * 0.4, 0.0, 1.0)
        self.frustration = _clip(self.frustration * 0.7, 0.0, 1.0)
        self.last_send = now
        self.save()

    def record_blocked(self, now: float | None = None, urge: float = 0.7) -> None:
        now = float(now if now is not None else time.time())
        self._prune(now)
        self.blocked.append(now)
        self.attempts += 1
        self.blocked_count += 1
        self.urge = _clip(max(self.urge, float(urge)), 0.0, 1.0)
        self.frustration = _clip(self.frustration + 0.12, 0.0, 1.0)
        self.expected_success = _clip(self.expected_success - self.lr * (1.0 - 0.2), 0.0, 1.0)
        self.last_block = now
        self.save()

    def should_hold(self, now: float | None = None) -> bool:
        if self.limit <= 0:
            return False
        now = float(now if now is not None else time.time())
        # learned: after repeated hard blocks, stop trying for a while
        return (self.blocked_count >= 3 and self.expected_success < 0.25
                and now - self.last_block < 6 * 3600.0)

    def record_hold(self) -> None:
        self.held_count += 1
        self.urge = _clip(self.urge + 0.05, 0.0, 1.0)
        self.save()

    def record_forced(self) -> None:
        self.forced_count += 1
        self.save()

    def context(self) -> dict:
        self._prune(time.time())
        return {
            "limit_per_hour": self.limit,
            "sent_last_hour": len(self.sent),
            "blocked_count": self.blocked_count,
            "attempts": self.attempts,
            "held_count": self.held_count,
            "forced_count": self.forced_count,
            "urge": round(self.urge, 3),
            "frustration": round(self.frustration, 3),
            "expected_success": round(self.expected_success, 3),
            "learned_hold": self.should_hold(),
        }

    def prompt_text(self) -> str:
        c = self.context()
        return ("群限额：过去一小时已发 %d/%d，想发但被挡 %d 次，"
                "预期成功率 %.2f，烦躁 %.2f。" %
                (c["sent_last_hour"], c["limit_per_hour"], c["blocked_count"],
                 c["expected_success"], c["frustration"]))

    def load(self) -> None:
        if not self.path:
            return
        try:
            d = json.loads(Path(self.path).read_text(encoding="utf-8"))
            self.sent = [float(x) for x in d.get("sent", [])]
            self.blocked = [float(x) for x in d.get("blocked", [])]
            self.urge = float(d.get("urge", 0.0))
            self.frustration = float(d.get("frustration", 0.0))
            self.expected_success = float(d.get("expected_success", 0.6))
            self.blocked_count = int(d.get("blocked_count", 0))
            self.attempts = int(d.get("attempts", 0))
            self.held_count = int(d.get("held_count", 0))
            self.forced_count = int(d.get("forced_count", 0))
            self.last_block = float(d.get("last_block", 0.0))
            self.last_send = float(d.get("last_send", 0.0))
        except Exception:
            pass

    def save(self) -> None:
        if not self.path:
            return
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.path).write_text(json.dumps({
                "sent": self.sent, "blocked": self.blocked, "urge": self.urge,
                "frustration": self.frustration, "expected_success": self.expected_success,
                "blocked_count": self.blocked_count, "attempts": self.attempts,
                "held_count": self.held_count, "forced_count": self.forced_count,
                "last_block": self.last_block,
                "last_send": self.last_send,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
