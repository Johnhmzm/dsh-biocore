from __future__ import annotations

from typing import Dict

from .noise import clip
from .types import Appraisal, Event


class Attention:
    """A minimal salience gate: what reaches working memory / focus."""

    def __init__(self):
        self.focus: str | None = None
        self.last_score = 0.0

    def update(self, ev: Event, ap: Appraisal, drives) -> float:
        score = (
            0.25 * ap.novelty
            + 0.25 * ap.relevance
            + 0.20 * ap.threat
            + 0.20 * abs(ap.valence)
            + 0.10 * abs(ap.social)
        )
        self.last_score = clip(score, 0.0, 1.0)
        if self.last_score >= 0.45 and ev.speaker:
            self.focus = ev.speaker
        return self.last_score

    def snapshot(self) -> Dict[str, object]:
        return {"focus": self.focus, "last_score": round(self.last_score, 4)}
