"""Cold / active / warm session window, inspired by Animus heartbeat sessions."""
from __future__ import annotations


class SessionWindow:
    def __init__(self, warmth_s: float = 900.0):
        self.warmth_s = float(warmth_s)
        self.state = "cold"
        self.last_active = 0.0
        self.warm_until = 0.0

    def touch(self, now: float) -> bool:
        was_warm = self.state == "warm" and float(now) <= self.warm_until
        self.state = "active"
        self.last_active = float(now)
        return bool(was_warm)

    def finish(self, now: float) -> None:
        self.state = "warm"
        self.warm_until = float(now) + self.warmth_s

    def tick(self, now: float) -> None:
        if self.state == "warm" and float(now) > self.warm_until:
            self.state = "cold"

    def hint(self, now: float) -> str:
        if self.state == "cold":
            return ""
        if self.state == "warm" and float(now) <= self.warm_until:
            return "处于连续对话中，接着刚才说；不要重新自我介绍，不要重复问已经知道的信息。"
        return ""
