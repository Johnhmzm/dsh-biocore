from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Candidate:
    content: str
    source: str
    salience: float
    t: float = 0.0


@dataclass
class GlobalWorkspace:
    """LIDA-style competition: candidates compete, winner is broadcast."""

    capacity: int = 5
    winner: Candidate | None = None
    broadcast: List[Candidate] = field(default_factory=list)
    history: List[Candidate] = field(default_factory=list)

    def compete(self, candidates: List[Candidate], now: float = 0.0) -> Candidate | None:
        clean = [c for c in candidates if c.content and c.salience > 0]
        if not clean:
            self.winner = None
            self.broadcast = []
            return None
        ranked = sorted(clean, key=lambda c: c.salience, reverse=True)[: self.capacity]
        exps = [math.exp(3.0 * c.salience) for c in ranked]
        total = sum(exps) or 1.0
        for c, e in zip(ranked, exps):
            c.salience = round(e / total, 4)
        self.winner = ranked[0]
        self.broadcast = ranked
        self.history.append(self.winner)
        self.history = self.history[-20:]
        return self.winner

    def context(self) -> Dict[str, object]:
        return {
            "winner": self.winner.content if self.winner else None,
            "source": self.winner.source if self.winner else None,
            "broadcast": [{"content": c.content, "source": c.source} for c in self.broadcast],
        }

    def snapshot(self) -> Dict[str, object]:
        return {
            "winner": self.winner.content if self.winner else None,
            "source": self.winner.source if self.winner else None,
            "n_broadcast": len(self.broadcast),
            "history": [c.content for c in self.history[-5:]],
        }
