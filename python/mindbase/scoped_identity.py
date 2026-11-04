"""Scoped identity framework.

One CoreSelf, multiple PresentationState instances, hard memory scopes,
and a one-way PublicDigest from group -> private.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CoreSelf:
    persona: str = ""
    values: Dict[str, float] = field(default_factory=dict)
    voice: str = ""
    body_anchor: Dict[str, float] = field(default_factory=dict)

    def presentation(self, channel: str, audience_id: str,
                     style: Optional[Dict[str, float]] = None,
                     relationship: float = 0.0) -> "PresentationState":
        return PresentationState(core=self, channel=str(channel), audience_id=str(audience_id),
                                 style=dict(style or {}), relationship=float(relationship))


@dataclass
class PresentationState:
    core: CoreSelf
    channel: str
    audience_id: str
    style: Dict[str, float] = field(default_factory=dict)
    relationship: float = 0.0

    def effective(self) -> Dict[str, float]:
        out = dict(self.core.values)
        for key, value in self.style.items():
            if key in out:
                out[key] = max(0.0, min(1.0, float(out[key]) + float(value)))
        return out


@dataclass(frozen=True)
class MemoryScope:
    kind: str  # private | group | public
    owner: str = ""

    def can_read(self, reader: "MemoryScope") -> bool:
        if self.kind == "public":
            return True
        if self.kind == "private":
            return reader.kind == "private" and reader.owner == self.owner
        if self.kind == "group":
            return (reader.kind == "group" and reader.owner == self.owner) or reader.kind == "private"
        return False


@dataclass
class DigestEntry:
    group_id: str
    summary: str
    tags: List[str] = field(default_factory=list)


class PublicDigest:
    """Group -> private one-way bus. Only sanitized public summaries."""

    def __init__(self, max_entries: int = 200, path: Path | str | None = None):
        self.entries: List[DigestEntry] = []
        self.max_entries = int(max_entries)
        self.path = Path(path) if path else None
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [DigestEntry(**x) for x in data][-self.max_entries:]
        except Exception:
            self.entries = []

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([x.__dict__ for x in self.entries], ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except Exception:
            pass

    def publish(self, group_id: str, summary: str, tags: Optional[List[str]] = None) -> None:
        text = str(summary or "").strip()
        if not text:
            return
        self.entries.append(DigestEntry(str(group_id), text[:400], list(tags or [])))
        self.entries = self.entries[-self.max_entries:]
        self._save()

    def read(self, group_id: str = "", limit: int = 5) -> str:
        rows = [x for x in self.entries if not group_id or x.group_id == group_id]
        return "\n".join(x.summary for x in rows[-max(1, int(limit)):])


class ConsistencyMonitor:
    """Core values must not change across presentations; style may change."""

    def check(self, core: CoreSelf, presentations: List[PresentationState]) -> Dict[str, object]:
        out = {"ok": True, "violations": []}
        for p in presentations:
            if p.core is not core and p.core != core:
                out["ok"] = False
                out["violations"].append("core_mismatch:" + p.channel)
        return out
