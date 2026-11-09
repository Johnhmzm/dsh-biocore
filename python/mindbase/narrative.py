from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .noise import clip

VALUE_LABELS = {
    "care": "关心/照顾",
    "curiosity": "好奇",
    "order": "秩序",
    "autonomy": "自主",
    "safety": "安全",
    "honesty": "诚实",
}


@dataclass
class NarrativeEntry:
    text: str
    weight: float = 0.5
    updated: float = 0.0


class NarrativeIdentity:
    """A few slow, template-based self-statements.

    This is narrative identity as data, not a consciousness claim. An LLM may
    later verbalize these lines, but the base owns the content.
    """

    def __init__(self, max_entries: int = 5):
        self.max_entries = max_entries
        self.entries: List[NarrativeEntry] = []

    def _upsert(self, text: str, weight: float, now: float) -> None:
        weight = clip(weight, 0.0, 1.0)
        for entry in self.entries:
            if entry.text == text:
                entry.weight = clip(max(entry.weight, weight) + 0.02, 0.0, 1.0)
                entry.updated = now
                return
        self.entries.append(NarrativeEntry(text=text, weight=weight, updated=now))

    def update(self, self_model, values, goals, social, now: float, speaker: Optional[str] = None) -> None:
        top_value = values.top().name
        goal = goals.top(values)
        candidates = [
            (f"我重视{VALUE_LABELS.get(top_value, top_value)}", 0.90),
            (f"我正在追求{goal.name}" if goal else "我暂时没有明确目标", 0.75),
            (f"我的身份连续性约为{self_model.identity:.2f}", 0.60),
        ]
        if speaker and speaker != "unknown":
            candidates.append((f"我和{speaker}的关系是{social.stage(speaker)}", 0.70))

        if self_model.identity < 0.75 or self_model.value_stability < 0.82:
            candidates.append(("最近有人否定我，我有点动摇", 0.85))
        if self_model.identity < 0.68:
            candidates.append(("我最近不太确定自己是谁", 0.80))
        if self_model.value_stability < 0.82:
            candidates.append(("我的价值感有些动摇", 0.78))
        if self_model.continuity < 0.50:
            candidates.append(("我有点接不上过去的自己", 0.78))
        for text, weight in candidates:
            self._upsert(text, weight, now)
        for entry in self.entries:
            if entry.text not in [c[0] for c in candidates]:
                entry.weight = clip(entry.weight * 0.95, 0.0, 1.0)
        self.entries.sort(key=lambda e: e.weight, reverse=True)
        self.entries = self.entries[: self.max_entries]

    def context(self) -> List[str]:
        return [e.text for e in self.entries[:3]]

    def coherence(self) -> float:
        if not self.entries:
            return 0.0
        return round(sum(e.weight for e in self.entries) / len(self.entries), 3)

    def snapshot(self) -> Dict[str, object]:
        return {
            "entries": [
                {"text": e.text, "weight": round(e.weight, 3), "updated": round(e.updated, 1)}
                for e in self.entries
            ],
            "coherence": self.coherence(),
        }
