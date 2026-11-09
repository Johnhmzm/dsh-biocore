from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

from .character_profile import merge
from .diary import DiaryStore


class UserProfileStore:
    """Per-user delta + diary. Base stays global; identity never leaks across users."""

    def __init__(self, root: Path, base: Dict[str, float]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.base = dict(base)

    def _dir(self, user_id: str) -> Path:
        key = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        path = self.root / key
        path.mkdir(parents=True, exist_ok=True)
        meta = path / "user.json"
        if not meta.exists():
            meta.write_text(json.dumps({"user_id": str(user_id)}, ensure_ascii=False), encoding="utf-8")
        return path

    def delta_path(self, user_id: str) -> Path:
        return self._dir(user_id) / "delta.json"

    def load_meta(self, user_id: str) -> Dict[str, float]:
        path = self._dir(user_id) / "meta.json"
        if not path.exists():
            return {"age": 0.0}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"age": 0.0}

    def save_meta(self, user_id: str, meta: Dict[str, float]) -> None:
        (self._dir(user_id) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_delta(self, user_id: str) -> Dict[str, float]:
        path = self.delta_path(user_id)
        if not path.exists():
            zeros = {k: 0.0 for k in self.base}
            path.write_text(json.dumps(zeros, ensure_ascii=False, indent=2), encoding="utf-8")
            return zeros
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {k: 0.0 for k in self.base}

    def save_delta(self, user_id: str, delta: Dict[str, float]) -> None:
        self.delta_path(user_id).write_text(json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8")

    def effective(self, user_id: str) -> Dict[str, float]:
        return merge(self.base, self.load_delta(user_id))

    def diary(self) -> DiaryStore:
        return DiaryStore(self.root / "_diary")

    def users(self):
        out = []
        for path in self.root.iterdir():
            meta = path / "user.json"
            if path.is_dir() and meta.exists():
                try:
                    out.append(str(json.loads(meta.read_text(encoding="utf-8")).get("user_id")))
                except Exception:
                    continue
        return out
