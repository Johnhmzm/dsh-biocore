from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict


class ProactiveProfile:
    """Per-user Beta-Bernoulli acceptance model by hour and topic."""

    ALPHA = 2.0
    BETA = 2.0

    def __init__(self, root: Path, user_id: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.user_id = str(user_id)
        self.data = self._load()

    def _path(self) -> Path:
        key = hashlib.sha256(self.user_id.encode("utf-8")).hexdigest()[:16]
        return self.root / (key + ".json")

    def _blank(self) -> Dict[str, object]:
        return {"hours": {}, "topics": {}, "sent": 0, "accepted": 0, "ignored": 0,
                "expectation": 0.5, "fear": 0.0, "joy": 0.0, "last_update": 0.0,
                "refractory_scale": 1.0, "delay_mean": 300.0, "delay_n": 0, "settle_scale": 1.0}

    def _load(self) -> Dict[str, object]:
        path = self._path()
        if not path.exists():
            return self._blank()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            base = self._blank()
            base.update(data if isinstance(data, dict) else {})
            return base
        except Exception:
            return self._blank()

    def decay(self, now: float) -> None:
        """Apply time decay to learned fear/joy and persist the clock.

        The previous implementation mutated last_update but never saved it, so
        a reloaded profile always believed it had just been created and the
        learned fear never decayed.  Saving here fixes that.
        """
        try:
            last = float(self.data.get("last_update", 0.0) or 0.0)
            now = float(now)
            if last <= 0:
                self.data["last_update"] = now
                self.save()
                return
            hours = max(0.0, (now - last) / 3600.0)
            if hours * 3600.0 < 30.0:
                return
            self.data["fear"] = max(0.0, float(self.data.get("fear", 0.0)) * (2.718281828 ** (-hours / 12.0)))
            self.data["joy"] = max(0.0, float(self.data.get("joy", 0.0)) * (2.718281828 ** (-hours / 3.0)))
            self.data["last_update"] = now
            self.save()
        except Exception:
            pass

    def settle_after(self) -> float:
        mean_delay = float(self.data.get("delay_mean", 300.0))
        scale = float(self.data.get("settle_scale", 1.0))
        return max(120.0, min(86400.0, mean_delay * scale))

    def anticipation(self) -> float:
        expectation = float(self.data.get("expectation", 0.5))
        fear = float(self.data.get("fear", 0.0))
        return round(max(0.0, min(1.0, fear * (1.0 - expectation))), 4)

    def save(self) -> None:
        self._path().write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")

    def record(self, hour: int, topic: str, accepted: bool, delay: float = 0.0, sentiment: float = 0.0, anxiety: float = 0.0) -> None:
        hour_key = str(int(hour) % 24)
        hours = self.data.setdefault("hours", {})
        cell = hours.setdefault(hour_key, {"a": self.ALPHA, "b": self.BETA})
        topics = self.data.setdefault("topics", {})
        topic_key = str(topic or "general")[:24]
        tcell = topics.setdefault(topic_key, {"a": self.ALPHA, "b": self.BETA})
        outcome = 1.0 if accepted and float(sentiment) >= 0 else 0.0
        expectation = float(self.data.get("expectation", 0.5))
        self.data["expectation"] = max(0.0, min(1.0, expectation + 0.2 * (outcome - expectation)))
        ax = max(0.0, min(1.0, float(anxiety)))
        if outcome > 0:
            self.data["fear"] = max(0.0, float(self.data.get("fear", 0.0)) - 0.05 * (1.0 + ax))
            self.data["joy"] = min(1.0, float(self.data.get("joy", 0.0)) + 0.10 * (1.0 + ax))
            self.data["refractory_scale"] = max(0.5, float(self.data.get("refractory_scale", 1.0)) * 0.95)
            n_delay = int(self.data.get("delay_n", 0))
            mean_delay = float(self.data.get("delay_mean", 300.0))
            mean_delay = (mean_delay * n_delay + max(0.0, float(delay))) / (n_delay + 1)
            self.data["delay_mean"] = min(86400.0, mean_delay)
            self.data["delay_n"] = n_delay + 1
            self.data["settle_scale"] = max(0.5, float(self.data.get("settle_scale", 1.0)) * 0.95)
            cell["a"] = float(cell.get("a", self.ALPHA)) + 1.0
            tcell["a"] = float(tcell.get("a", self.ALPHA)) + 1.0
            self.data["accepted"] = int(self.data.get("accepted", 0)) + 1
        else:
            self.data["fear"] = min(0.95, float(self.data.get("fear", 0.0)) + 0.10 * (1.0 + ax))
            self.data["joy"] = max(0.0, float(self.data.get("joy", 0.0)) - 0.05)
            factor = 1.5 if float(sentiment) < 0 else 1.1
            self.data["refractory_scale"] = min(12.0, float(self.data.get("refractory_scale", 1.0)) * factor)
            self.data["settle_scale"] = min(8.0, float(self.data.get("settle_scale", 1.0)) * 1.2)
            cell["b"] = float(cell.get("b", self.BETA)) + 1.0
            tcell["b"] = float(tcell.get("b", self.BETA)) + 1.0
            self.data["ignored"] = int(self.data.get("ignored", 0)) + 1
        self.data["sent"] = int(self.data.get("sent", 0)) + 1
        self.save()

    def observe(self, reward: float, hour: int, topic: str, delay: float = 0.0, now: float = 0.0) -> float:
        """Learn from a continuous outcome reward (no accept/reject threshold).

        Positive reward reinforces the hour/topic Beta cell; negative reward
        adds failure mass.  Expectation/fear/joy and the learned wait horizon
        move by exponential moving averages driven only by observed rewards,
        so the profile adapts to the user instead of following fixed rules.
        """
        try:
            r = max(-2.0, min(1.0, float(reward)))
        except Exception:
            return 0.0
        try:
            hour_key = str(int(hour) % 24)
            topic_key = str(topic or "general")[:24]
            pos = max(0.0, r)
            neg = max(0.0, -r)
            hours = self.data.setdefault("hours", {})
            cell = hours.setdefault(hour_key, {"a": self.ALPHA, "b": self.BETA})
            cell["a"] = float(cell.get("a", self.ALPHA)) + pos
            cell["b"] = float(cell.get("b", self.BETA)) + neg
            topics = self.data.setdefault("topics", {})
            tcell = topics.setdefault(topic_key, {"a": self.ALPHA, "b": self.BETA})
            tcell["a"] = float(tcell.get("a", self.ALPHA)) + pos
            tcell["b"] = float(tcell.get("b", self.BETA)) + neg
            expectation = float(self.data.get("expectation", 0.5))
            self.data["expectation"] = max(0.0, min(1.0, expectation + 0.15 * (r - expectation)))
            fear = float(self.data.get("fear", 0.0))
            joy = float(self.data.get("joy", 0.0))
            self.data["fear"] = max(0.0, min(0.95, fear - 0.10 * pos + 0.12 * neg))
            self.data["joy"] = max(0.0, min(1.0, joy + 0.10 * pos - 0.08 * neg))
            settle = float(self.data.get("settle_scale", 1.0))
            refractory = float(self.data.get("refractory_scale", 1.0))
            if r > 0.0:
                n_delay = int(self.data.get("delay_n", 0))
                mean_delay = float(self.data.get("delay_mean", 300.0))
                delay = max(0.0, min(86400.0, float(delay)))
                self.data["delay_mean"] = (mean_delay * n_delay + delay) / (n_delay + 1)
                self.data["delay_n"] = n_delay + 1
                self.data["settle_scale"] = max(0.5, settle * 0.97)
                self.data["refractory_scale"] = max(0.5, refractory * 0.97)
            else:
                self.data["settle_scale"] = min(8.0, settle * 1.05)
                self.data["refractory_scale"] = min(12.0, refractory * 1.05)
            self.data["sent"] = int(self.data.get("sent", 0)) + 1
            if r > 0.0:
                self.data["accepted"] = int(self.data.get("accepted", 0)) + 1
            elif r < 0.0:
                self.data["ignored"] = int(self.data.get("ignored", 0)) + 1
            self.save()
            return r
        except Exception:
            return r

    def _rate(self, cell) -> float:
        a = float(cell.get("a", self.ALPHA))
        b = float(cell.get("b", self.BETA))
        return a / max(1e-6, a + b)

    def acceptance(self, hour: int, topic: str) -> float:
        hours = self.data.get("hours", {})
        topics = self.data.get("topics", {})
        h = self._rate(hours.get(str(int(hour) % 24), {"a": self.ALPHA, "b": self.BETA}))
        t = self._rate(topics.get(str(topic or "general")[:24], {"a": self.ALPHA, "b": self.BETA}))
        return 0.7 * h + 0.3 * t

    def threshold(self, hour: int, topic: str, base: float) -> float:
        rate = self.acceptance(hour, topic)
        adjusted = float(base) - 0.20 * (rate - 0.5)
        return max(0.40, min(0.85, round(adjusted, 4)))

    def summary(self) -> Dict[str, object]:
        return {"sent": self.data.get("sent", 0), "accepted": self.data.get("accepted", 0),
                "ignored": self.data.get("ignored", 0), "hours": self.data.get("hours", {}),
                "topics": self.data.get("topics", {}),
                "expectation": round(float(self.data.get("expectation", 0.5)), 4),
                "anticipation": self.anticipation(),
                "fear": round(float(self.data.get("fear", 0.0)), 4),
                "joy": round(float(self.data.get("joy", 0.0)), 4),
                "refractory_scale": round(float(self.data.get("refractory_scale", 1.0)), 4),
                "delay_mean": round(float(self.data.get("delay_mean", 300.0)), 2),
                "settle_after": round(self.settle_after(), 2)}
