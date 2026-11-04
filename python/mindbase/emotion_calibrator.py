"""Learned emotion calibration head (char bigrams + raw LLM emotion)."""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path
from typing import Optional


class EmotionCalibrator:
    def __init__(self, path: Path | str = "mindbase/data/emotion_calibrator.json"):
        self.path = Path(path)
        self.model = {}
        try:
            self.model = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.model = {}

    @property
    def enabled(self) -> bool:
        return bool(self.model)

    def _features(self, text: str, raw: str):
        t = "".join(str(text or "").split())
        out = collections.Counter()
        for i in range(max(0, len(t) - 1)):
            g = t[i:i + 2]
            if all("\u4e00" <= c <= "\u9fff" for c in g):
                out[g] += 1
        out["__raw_" + str(raw or "neutral")] += 1
        return {g: n for g, n in out.items() if g in set(self.model.get("vocab", []))}

    def predict(self, text: str, raw_emotion: str) -> str:
        if not self.enabled:
            return str(raw_emotion or "neutral")
        classes = list(self.model.get("classes") or [])
        if not classes:
            return str(raw_emotion or "neutral")
        f = self._features(text, raw_emotion)
        n_docs = sum(self.model.get("prior", {}).values()) or 1
        best, best_s = str(raw_emotion or "neutral"), -1e18
        for c in classes:
            s = math.log((self.model["prior"].get(c, 0) + 1) / (n_docs + len(classes)))
            denom = self.model["totals"].get(c, 0) + max(1, len(self.model.get("vocab", [])))
            for g, n in f.items():
                s += n * math.log((self.model["counts"].get(c, {}).get(g, 0) + 1) / denom)
            if s > best_s:
                best, best_s = c, s
        return best
