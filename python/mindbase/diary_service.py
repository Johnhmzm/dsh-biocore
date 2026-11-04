from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict

from .diary import DiaryEntry, DiaryStore, summarize_with_llm
from .episode_log import load_episodes


class DiaryService:
    """Once per user per day: summarize that user's episodes into a diary entry."""

    def __init__(self, store: DiaryStore, client, persona: str = "", episodes_path: Path | None = None):
        self.store = store
        self.client = client
        self.persona = persona
        self.episodes_path = episodes_path

    def run_once(self, users=None) -> Dict[str, str]:
        today = datetime.now().strftime("%Y-%m-%d")
        rows = load_episodes(self.episodes_path) if self.episodes_path else load_episodes()
        by_user: Dict[str, list] = {}
        allow = set(str(x) for x in users) if users is not None else None
        for row in rows:
            uid = str(row.get("user_id") or "")
            if uid and (allow is None or uid in allow):
                by_user.setdefault(uid, []).append(row)
        out = {}
        for uid, episodes in by_user.items():
            if self.store.day(uid, today):
                continue
            try:
                content = summarize_with_llm(self.client, uid, episodes, self.persona)
                if content:
                    self.store.replace_day(uid, today, DiaryEntry(user_id=uid, group_id="", date=today, content=content, source="auto"))
                    out[uid] = "written"
            except Exception as exc:
                out[uid] = "error: " + str(exc)
        return out
