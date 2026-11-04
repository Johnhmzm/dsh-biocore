from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict

RECOVER_SECONDS = 300.0
HARD_WINDOW = 600.0
HARD_LIMIT = 3


@dataclass
class GroupMessage:
    text: str
    speaker: str = "user"
    ts: float = 0.0
    mentions_bot: bool = False


@dataclass
class MockSender:
    sent: List[str] = field(default_factory=list)

    def send(self, text: str) -> None:
        self.sent.append(text)


class GroupState:
    def __init__(self):
        self.messages: List[GroupMessage] = []
        self.budget = 1.0
        self.last_update = 0.0
        self.last_bot_at = -1e9
        self.sent_times: List[float] = []

    def recover(self, now: float) -> None:
        if now > self.last_update:
            self.budget = min(1.0, self.budget + (now - self.last_update) / RECOVER_SECONDS)
            self.last_update = now

    def recent(self, now: float, window: float = 600.0) -> List[GroupMessage]:
        return [m for m in self.messages if now - m.ts <= window]

    def hard_limit_hit(self, now: float) -> bool:
        self.sent_times = [t for t in self.sent_times if now - t <= HARD_WINDOW]
        return len(self.sent_times) >= HARD_LIMIT


class ProactiveGroup:
    def __init__(self, sender: Optional[MockSender] = None):
        self.state = GroupState()
        self.sender = sender or MockSender()

    def observe(self, text: str, speaker: str = "user", ts: float = 0.0, mentions_bot: bool = False) -> None:
        self.state.messages.append(GroupMessage(text=text, speaker=speaker, ts=ts, mentions_bot=mentions_bot))

    def candidate(self, now: float) -> Optional[str]:
        recent = self.state.recent(now)
        if not recent:
            return None
        last = recent[-1]
        if last.mentions_bot:
            return last.text
        # no keyword rules: only use the last message as a topic cue when there is prior context
        if len(recent) >= 3:
            return last.text
        return None

    def score(self, candidate: str, now: float) -> float:
        recent = self.state.recent(now)
        if not recent or not candidate:
            return 0.0
        def grams(text: str):
            text = "".join(str(text).split())
            return {text[i:i + 2] for i in range(max(0, len(text) - 1))}

        cand_g = grams(candidate)
        prior = recent[:-1][-3:]
        rel = 0.0
        if prior and cand_g:
            for m in prior:
                g = grams(m.text)
                if g:
                    rel = max(rel, len(cand_g & g) / max(1, len(cand_g | g)))
        addressed = 1.0 if any(m.mentions_bot for m in recent[-3:]) else 0.0
        silence = min(1.0, max(0.0, (now - self.state.last_bot_at) / 600.0))
        return round(0.45 * rel + 0.25 * addressed + 0.20 * self.state.budget + 0.10 * silence, 4)

    def next_check(self, now: float) -> float:
        recent = self.state.recent(now)
        if not recent:
            return 120.0
        if any(m.mentions_bot for m in recent[-3:]):
            return 10.0
        silence = max(0.0, now - self.state.last_bot_at)
        budget = max(0.0, min(1.0, self.state.budget))
        # state-driven delay, no fixed 90s tick
        delay = 20.0 + 120.0 * (1.0 - budget) + 30.0 * (1.0 - min(1.0, silence / 300.0))
        return round(max(8.0, min(240.0, delay)), 2)

    def tick(self, now: float) -> Optional[str]:
        self.state.recover(now)
        if self.state.hard_limit_hit(now):
            return None
        cand = self.candidate(now)
        if cand is None:
            return None
        min_gap = max(10.0, 90.0 * (1.0 - self.state.budget))
        if now - self.state.last_bot_at < min_gap:
            return None
        if self.score(cand, now) < 0.55:
            return None
        self.state.budget *= 0.50
        self.state.last_bot_at = now
        self.state.sent_times.append(now)
        self.sender.send(cand)
        return cand

    def snapshot(self) -> Dict[str, object]:
        return {"budget": round(self.state.budget, 3), "sent": list(self.sender.sent), "n_messages": len(self.state.messages)}
