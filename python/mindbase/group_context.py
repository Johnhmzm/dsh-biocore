"""Per-group recent context buffer (who said what)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, List


class GroupContext:
    def __init__(self, path: Path | str, max_rows: int = 24):
        self.path = Path(path)
        self.max_rows = int(max_rows)
        self.lock = threading.Lock()
        self.rows: List[Dict[str, object]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.rows = data[-self.max_rows:]
        except Exception:
            self.rows = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.rows[-self.max_rows:], ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except Exception:
            pass

    def record(self, speaker: str, text: str, reply: str) -> None:
        with self.lock:
            self.rows.append({"t": time.time(), "speaker": str(speaker or "群友"),
                              "text": str(text or "")[:300], "reply": str(reply or "")[:300]})
            self.rows = self.rows[-self.max_rows:]
            self._save()

    def rows_recent(self, seconds: float = 180.0, now: float = 0.0) -> List[Dict[str, object]]:
        now = float(now or time.time())
        with self.lock:
            return [r for r in self.rows if float(r.get("t", 0.0)) >= now - float(seconds)]

    def set_last_reply(self, reply: str) -> None:
        with self.lock:
            if self.rows:
                self.rows[-1]["reply"] = str(reply or "")[:300]
                self._save()

    def recent(self, limit: int = 8, include_reply: bool = False) -> str:
        with self.lock:
            rows = self.rows[-max(1, int(limit)):]
        out = []
        for r in rows:
            txt = str(r.get('text') or '')
            if '[图片：' in txt and len(txt) > 180:
                txt = txt[:180] + '…'
            line = f"{r.get('speaker')}：{txt}"
            if include_reply:
                line += f" / 她：{r.get('reply')}"
            out.append(line)
        return "\n".join(out)
