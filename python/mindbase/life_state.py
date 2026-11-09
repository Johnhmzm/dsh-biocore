"""Invisible daily life for the companion.

The bot does not narrate activities in chat. The life state only changes
energy / attention / stress / mood and is persisted, so she has a day of her
own even when nobody is talking to her.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict

from .noise import clip

ACTIVITIES = {
    "rest": {"label": "休息", "minutes": 35, "energy": 0.10, "stress": -0.10,
             "curiosity": -0.02, "social": -0.01, "attention": 0.20},
    "read": {"label": "看东西", "minutes": 30, "energy": -0.03, "stress": -0.04,
             "curiosity": 0.08, "social": 0.00, "attention": 0.75},
    "music": {"label": "听歌", "minutes": 25, "energy": 0.01, "stress": -0.05,
              "curiosity": 0.02, "social": 0.01, "attention": 0.45},
    "walk": {"label": "走动", "minutes": 20, "energy": -0.04, "stress": -0.08,
             "curiosity": 0.03, "social": 0.02, "attention": 0.35},
    "write": {"label": "写东西", "minutes": 40, "energy": -0.05, "stress": 0.02,
              "curiosity": 0.06, "social": -0.02, "attention": 0.85},
    "game": {"label": "打游戏", "minutes": 30, "energy": -0.02, "stress": -0.06,
             "curiosity": 0.03, "social": 0.03, "attention": 0.65},
    "cook": {"label": "弄吃的", "minutes": 25, "energy": 0.02, "stress": -0.03,
             "curiosity": 0.02, "social": 0.02, "attention": 0.55},
}


@dataclass
class LifeState:
    activity: str = "rest"
    progress: float = 0.0
    started: float = 0.0
    last_tick: float = 0.0
    energy: float = 0.7
    attention: float = 0.2
    stress: float = 0.2
    mood: float = 0.0
    seed: int = 2026
    recent: list = field(default_factory=list)
    completed: list = field(default_factory=list)

    def __post_init__(self):
        if not self.started:
            self.started = time.time()
        if not self.last_tick:
            self.last_tick = time.time()
        self.rng = random.Random(int(self.seed))

    def _choose(self, mind, hour: int) -> str:
        curiosity = 0.5
        social = 0.5
        try:
            curiosity = float(mind.drives.values.get("curiosity", 0.5))
            social = float(mind.drives.values.get("social", 0.5))
        except Exception:
            pass
        stress = float(getattr(getattr(mind, "affect", None), "stress", self.stress))
        w = {
            "rest": 0.10 + (0.65 if self.energy < 0.35 else 0.0) + (0.20 if stress > 0.65 else 0.0),
            "read": 0.10 + 0.30 * curiosity,
            "music": 0.12 + 0.15 * (1.0 - stress),
            "walk": 0.08 + 0.22 * stress,
            "write": 0.05 + 0.28 * curiosity + (0.10 if 8 <= hour <= 22 else 0.0),
            "game": 0.06 + 0.16 * (1.0 - self.energy),
            "cook": 0.05 + (0.12 if 7 <= hour <= 9 or 17 <= hour <= 20 else 0.0),
        }
        names = list(w)
        weights = [max(0.01, w[n]) for n in names]
        total = sum(weights)
        r = self.rng.random() * total
        for n, x in zip(names, weights):
            r -= x
            if r <= 0:
                return n
        return "rest"

    def tick(self, now: float | None = None, mind=None) -> Dict[str, object]:
        now = float(now if now is not None else time.time())
        dt = max(0.0, now - float(self.last_tick or now))
        self.last_tick = now
        if dt <= 0:
            return self.snapshot()
        a = ACTIVITIES.get(self.activity) or ACTIVITIES["rest"]
        minutes = max(0.0, dt / 60.0)
        duration = max(1.0, float(a["minutes"]))
        self.progress = min(1.0, self.progress + minutes / duration)
        self.energy = clip(self.energy + a["energy"] * minutes / 30.0, 0.0, 1.0)
        self.stress = clip(self.stress + a["stress"] * minutes / 30.0, 0.0, 1.0)
        self.mood = clip(self.mood + (a["stress"] * -0.5) * minutes / 30.0, -1.0, 1.0)
        self.attention = clip(a["attention"], 0.0, 1.0)
        # tiny internal effects only; nothing is narrated in chat
        try:
            mind.affect.stress = clip(0.995 * float(mind.affect.stress) + 0.005 * self.stress, 0.0, 1.0)
            mind.affect.v = clip(0.997 * float(mind.affect.v) + 0.003 * self.mood, -1.0, 1.0)
            if hasattr(mind.drives, "values"):
                mind.drives.values["curiosity"] = clip(
                    0.995 * float(mind.drives.values.get("curiosity", 0.5)) + 0.005 * (0.5 + a["curiosity"]), 0.0, 1.0)
                mind.drives.values["social"] = clip(
                    0.995 * float(mind.drives.values.get("social", 0.5)) + 0.005 * (0.5 + a["social"]), 0.0, 1.0)
        except Exception:
            pass
        if self.progress >= 1.0:
            hour = time.localtime(now).tm_hour
            done = {"activity": self.activity, "label": a["label"], "at": now}
            self.completed = (self.completed + [done])[-10:]
            nxt = self._choose(mind, hour)
            self.recent = (self.recent + [{"activity": self.activity, "at": now}])[-5:]
            self.activity = nxt
            self.progress = 0.0
            self.started = now
        return self.snapshot()

    def consume_completion(self) -> list:
        out = list(self.completed)
        self.completed = []
        return out

    @staticmethod
    def emotional_bias(activity: str) -> dict:
        table = {
            "rest": {"fear": -0.03, "anger": -0.03, "loneliness": -0.02},
            "read": {"surprise": 0.02, "trust": 0.02},
            "music": {"joy": 0.04, "sadness": -0.03, "trust": 0.01},
            "walk": {"joy": 0.03, "fear": -0.02, "loneliness": -0.02},
            "write": {"joy": 0.02, "trust": 0.02, "anger": 0.01},
            "game": {"joy": 0.04, "surprise": 0.02},
            "cook": {"joy": 0.03, "trust": 0.02},
        }
        return dict(table.get(str(activity), {}))

    def phrase(self) -> str:
        a = ACTIVITIES.get(self.activity) or ACTIVITIES["rest"]
        return "在%s，还没弄完" % a["label"]

    def snapshot(self) -> Dict[str, object]:
        return {
            "activity": self.activity,
            "progress": round(self.progress, 3),
            "energy": round(self.energy, 3),
            "attention": round(self.attention, 3),
            "stress": round(self.stress, 3),
            "mood": round(self.mood, 3),
            "recent": self.recent[-3:],
        }

    def save(self, path: Path | str) -> None:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def load(cls, path: Path | str) -> "LifeState":
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            obj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
            return obj
        except Exception:
            return cls()
