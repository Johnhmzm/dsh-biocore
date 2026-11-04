from __future__ import annotations

import time


class ProactiveGate:
    """Pure daily-cap / pause / quiet-hours gate (testable, no I/O)."""

    def __init__(self, daily_cap: int = 6, ignored_limit: int = 3, pause_seconds: float = 6 * 3600.0):
        self.daily_cap = int(daily_cap)
        self.ignored_limit = int(ignored_limit)
        self.pause_seconds = float(pause_seconds)
        self.daily = {}
        self.ignored = {}
        self.paused_until = {}

    def _day(self, now: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(now))

    def can_send(self, user_id: str, now: float, hour: int) -> tuple[bool, str]:
        uid = str(user_id)
        if 0 <= int(hour) < 7:
            return False, "quiet_hours"
        day = self._day(now)
        cell = self.daily.setdefault(uid, {"date": day, "count": 0})
        if cell.get("date") != day:
            cell["date"] = day
            cell["count"] = 0
        if int(cell.get("count", 0)) >= self.daily_cap:
            return False, "daily_cap"
        if now < float(self.paused_until.get(uid, 0.0)):
            return False, "paused"
        return True, "ok"

    def record_sent(self, user_id: str, now: float) -> None:
        uid = str(user_id)
        day = self._day(now)
        cell = self.daily.setdefault(uid, {"date": day, "count": 0})
        if cell.get("date") != day:
            cell["date"] = day
            cell["count"] = 0
        cell["count"] = int(cell.get("count", 0)) + 1

    def record_ignored(self, user_id: str, now: float) -> None:
        uid = str(user_id)
        n = int(self.ignored.get(uid, 0)) + 1
        self.ignored[uid] = n
        if n >= self.ignored_limit:
            self.paused_until[uid] = float(now) + self.pause_seconds
            self.ignored[uid] = 0

    def record_accepted(self, user_id: str) -> None:
        self.ignored[str(user_id)] = 0
