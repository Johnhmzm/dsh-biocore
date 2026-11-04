from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

DEFAULT = Path(__file__).resolve().parent / "data" / "interactions.jsonl"


class InteractionLogger:
    """Append-only interaction log. No LLM involved."""

    def __init__(self, path: Path | str | None = None, session: str | None = None):
        self.path = Path(path or DEFAULT)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session = session or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    def record(self, row: Dict[str, object]) -> None:
        row = dict(row)
        row.setdefault("session", self.session)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read(self, limit: int | None = None) -> list[Dict[str, object]]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[-limit:] if limit else rows
