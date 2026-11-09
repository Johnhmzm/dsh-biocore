from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict


class PreferenceStore:
    """Per-user low-dimensional style preferences learned from turn reward."""

    DIMS = ("length", "question", "diversity")

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        key = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        return self.root / (key + ".json")

    def load(self, user_id: str) -> Dict[str, float]:
        path = self._path(user_id)
        if not path.exists():
            return {k: 0.0 for k in self.DIMS}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {k: float(data.get(k, 0.0)) for k in self.DIMS}
        except Exception:
            return {k: 0.0 for k in self.DIMS}

    def update(self, user_id: str, reward: float, features: Dict[str, float], lr: float = 0.02) -> Dict[str, float]:
        w = self.load(user_id)
        r = max(-1.0, min(1.0, float(reward)))
        for k in self.DIMS:
            x = max(0.0, min(1.0, float(features.get(k, 0.0))))
            w[k] = max(-1.0, min(1.0, w[k] + lr * r * (x - 0.5)))
        self._path(user_id).write_text(json.dumps({k: round(v, 4) for k, v in w.items()}, ensure_ascii=False), encoding="utf-8")
        return w

    def hint(self, user_id: str) -> str:
        w = self.load(user_id)
        labels = {"length": "长度", "question": "提问", "diversity": "变化"}
        parts = []
        for k, v in w.items():
            if abs(v) < 0.002:
                continue
            direction = "偏多" if v > 0 else "偏少"
            parts.append(direction + labels.get(k, k))
        return "；".join(parts)
