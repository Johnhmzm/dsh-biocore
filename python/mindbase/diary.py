from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DiaryEntry:
    user_id: str
    group_id: str
    date: str
    content: str
    mood: str = ""
    tags: Optional[List[str]] = None
    source: str = "chat"

    def to_dict(self) -> Dict[str, object]:
        out = asdict(self)
        out["tags"] = list(self.tags or [])
        return out


class DiaryStore:
    """Per-user diary files. One user = one directory, one day = one jsonl file."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _user_dir(self, user_id: str) -> Path:
        key = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        path = self.root / key
        path.mkdir(parents=True, exist_ok=True)
        (path / "user.json").write_text(
            json.dumps({"user_id": str(user_id)}, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def append(self, entry: DiaryEntry) -> Path:
        d = self._user_dir(entry.user_id)
        path = d / (entry.date + ".jsonl")
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return path

    def replace_day(self, user_id: str, date: str, entry: DiaryEntry) -> Path:
        d = self._user_dir(user_id)
        path = d / (date + ".jsonl")
        path.write_text(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def day(self, user_id: str, date: str) -> List[Dict[str, object]]:
        path = self._user_dir(user_id) / (date + ".jsonl")
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows

    def recent(self, user_id: str, limit: int = 7) -> List[Dict[str, object]]:
        d = self._user_dir(user_id)
        rows: List[Dict[str, object]] = []
        for path in sorted(d.glob("*.jsonl"), reverse=True):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
            if len(rows) >= limit:
                break
        return rows[-limit:]

    def users(self) -> List[str]:
        out = []
        for path in self.root.iterdir():
            meta = path / "user.json"
            if path.is_dir() and meta.exists():
                try:
                    out.append(str(json.loads(meta.read_text(encoding="utf-8")).get("user_id")))
                except Exception:
                    continue
        return out


def summarize_with_llm(client, user_id: str, episodes: List[Dict[str, object]], persona: str = "") -> str:
    """Generate a first-person diary from one user's episodes. No keyword rules."""
    lines = []
    for ep in episodes[-40:]:
        text = str(ep.get("text") or "")
        reply = str(ep.get("reply") or "")
        if text or reply:
            lines.append("用户：" + text + "\\n她：" + reply)
    system = (persona.strip() + "\\n\\n" if persona else "") + (
        "用第一人称写一篇简短的日记，只写这一天和这个人的事。"
        "不要逐条复述对话，不要解释规则，不要编造未发生的事。"
    )
    user = "这个人：" + str(user_id) + "\\n对话：\\n" + "\\n".join(lines) + "\\n写 1-2 段日记。"
    from .types import LLMRequest
    req = LLMRequest(mode="semantic", urgency=0.5, max_tokens=400, reason="diary",
                     context={"system": system, "user": user})
    return str(client.generate(req) or "").strip()


def save_daily_from_episodes(store: DiaryStore, user_id: str, group_id: str, episodes: List[Dict[str, object]],
                             content: str, mood: str = "") -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return store.append(DiaryEntry(user_id=user_id, group_id=group_id, date=today,
                                  content=content, mood=mood, source="episodes"))
