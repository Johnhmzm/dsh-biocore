"""Proactive generation/iteration layer.

Base gives an urge; the generator produces candidate words; the learned policy
decides whether to send, and the iterator learns from accepted/ignored outcomes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Tuple

from .noise import clip

FEATURES = ("quiet", "social", "relationship", "acceptance", "recent", "time", "energy", "fatigue")


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, float(x)))))


def biological_cooldown(mind, base: float = 600.0) -> Tuple[float, Dict[str, float]]:
    """Human-like hurt recovery from affect/physiology/personality, not a fixed timer."""
    try:
        stress = float(mind.affect.stress)
    except Exception:
        stress = 0.0
    try:
        social = float(mind.drives.values.get("social", 0.5))
    except Exception:
        social = 0.5
    try:
        trust = float(mind.social.trust_for("web"))
    except Exception:
        trust = 0.5
    recovery = 0.7
    anxiety = 0.5
    try:
        recovery = float(getattr(mind.persona, "recovery_rate", 0.7))
        anxiety = float(getattr(mind.persona, "N", 0.5))
    except Exception:
        pass
    try:
        oxy = float(mind.physiology.oxytocin)
    except Exception:
        oxy = 0.5
    seconds = (float(base)
               * (0.45 + 1.6 * stress)
               * (1.25 - 0.55 * recovery)
               * (1.10 - 0.25 * trust)
               * (1.0 + 0.45 * anxiety)
               * (1.10 - 0.20 * social)
               * (1.05 - 0.10 * oxy))
    seconds = float(max(120.0, min(5400.0, seconds)))
    return round(seconds, 1), {"stress": round(stress, 3), "social": round(social, 3),
                              "trust": round(trust, 3), "recovery": round(recovery, 3),
                              "anxiety": round(anxiety, 3), "oxytocin": round(oxy, 3)}


def relationship_value(mind) -> float:
    try:
        stage = mind.social.stage("web")
    except Exception:
        stage = "stranger"
    return {"stranger": 0.0, "acquaintance": 0.25, "familiar": 0.5,
            "trusted": 0.75, "intimate": 1.0}.get(str(stage), 0.25)


def make_features(mind, now: float, last_user: float, last_proactive: float,
                  iterator: "ProactiveIterator", quiet_horizon: float = 1800.0,
                  body: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    quiet = clip((float(now) - float(last_user)) / max(1.0, float(quiet_horizon)), 0.0, 1.0) if last_user > 0 else 0.0
    try:
        social = clip(float(mind.drives.values.get("social", 0.5)), 0.0, 1.0)
    except Exception:
        social = 0.5
    recent = 1.0 if (last_proactive > 0 and float(now) - float(last_proactive) < 3600.0) else 0.0
    hour = float(__import__("time").localtime().tm_hour)
    time_feat = clip((hour - 8.0) / 14.0, 0.0, 1.0)
    b = body or {}
    energy = clip(float(b.get("energy", 0.7)), 0.0, 1.0)
    fatigue = clip(float(b.get("fatigue", 0.2)), 0.0, 1.0)
    return {"quiet": quiet, "social": social, "relationship": relationship_value(mind),
            "acceptance": iterator.acceptance, "recent": recent, "time": time_feat,
            "energy": energy, "fatigue": fatigue}


@dataclass
class ProactivePolicy:
    lr: float = 0.08
    weights: Dict[str, float] = field(default_factory=lambda: {
        "quiet": 1.4, "social": 1.8, "relationship": 0.8,
        "acceptance": 0.6, "recent": -1.2, "time": 0.2,
        "energy": 0.6, "fatigue": -0.9,
    })
    bias: float = -1.8

    def prob(self, feats: Dict[str, float]) -> float:
        z = self.bias + sum(self.weights.get(k, 0.0) * float(feats.get(k, 0.0)) for k in FEATURES)
        return sigmoid(z)

    def decide(self, feats: Dict[str, float], threshold: float = 0.55) -> Tuple[bool, float]:
        p = self.prob(feats)
        return p >= float(threshold), p

    def observe(self, feats: Dict[str, float], reward: float) -> Dict[str, float]:
        target = clip(float(reward), 0.0, 1.0)
        p = self.prob(feats)
        err = target - p
        for k in FEATURES:
            self.weights[k] = self.weights.get(k, 0.0) + self.lr * err * float(feats.get(k, 0.0))
        self.bias += self.lr * err
        return {"prob": round(p, 4), "target": target, "weights": {k: round(v, 3) for k, v in self.weights.items()},
                "bias": round(self.bias, 3)}


@dataclass
class ProactiveIterator:
    acceptance: float = 0.5
    alpha: float = 0.15
    sent: int = 0
    accepted: int = 0
    ignored: int = 0
    negative: int = 0
    last_result: str = "none"
    last_features: Dict[str, float] = field(default_factory=dict)

    def record_sent(self, feats: Dict[str, float]) -> None:
        self.sent += 1
        self.last_features = dict(feats)

    def record_outcome(self, reward: float, result: str | None = None) -> str:
        r = float(reward)
        result = result or ("accepted" if r >= 0.5 else ("negative" if r < 0.2 else "weak"))
        if result == "accepted":
            self.accepted += 1
        elif result == "negative":
            self.negative += 1
        else:
            self.ignored += 1
        self.acceptance = clip((1.0 - self.alpha) * self.acceptance + self.alpha * (1.0 if result == "accepted" else 0.0), 0.0, 1.0)
        self.last_result = result
        return result


def proactive_system_prompt(full_persona: str, recent: str, recall: str) -> str:
    return (
        "完整人设提示词（语义层，必须继续完整使用；不要改写人设）：\n" + full_persona +
        "\n\n现在没有新的用户消息。你要主动说一句话。\n"
        "依据最近对话和记忆，选一个具体、自然、不重复的话题；不要问“你在吗”，不要复述。\n"
        "不要每句都用同一个道具/称呼；最近说过的意象避免重复。\n"
        "只输出你要对用户说的那一句话，不要解释。\n"
        "最近对话：\n" + (recent or "（无）") +
        "\n记忆：\n" + (recall or "（无）")
    )



@dataclass
class ProactiveScheduler:
    """A background scheduler: generate, queue, detect ignored, learn."""
    generator: object
    policy: ProactivePolicy = field(default_factory=ProactivePolicy)
    iterator: ProactiveIterator = field(default_factory=ProactiveIterator)
    features_fn: object = None
    quiet_s: float = 300.0
    ignore_s: float = 300.0
    max_per_day: int = 4
    negative_cooldown_s: float = 1800.0
    cooldown_until: float = 0.0
    cooldown_duration: float = 0.0
    cooldown_bio: Dict[str, float] = field(default_factory=dict)
    on_ignored: object = None
    last_user: float = field(default_factory=lambda: __import__("time").time())
    last_proactive: float = 0.0
    pending: Dict[str, object] | None = None
    queue: list = field(default_factory=list)
    day: str = field(default_factory=lambda: __import__("time").strftime("%Y-%m-%d"))
    sent_today: int = 0
    last_generated: str = ""

    def _now(self, now=None) -> float:
        return float(now) if now is not None else float(__import__("time").time())

    def _features(self, now: float) -> Dict[str, float]:
        if self.features_fn is None:
            raise RuntimeError("features_fn required")
        return self.features_fn(now, self.last_user, self.last_proactive, self.iterator)

    def _record(self, reward: float, result: str | None = None) -> None:
        if not self.pending:
            return
        feats = dict(self.pending.get("features") or {})
        self.policy.observe(feats, reward)
        self.iterator.record_outcome(reward, result=result)
        self.pending = None

    def note_user(self, now=None, sentiment: float = 1.0, cooldown_s: float | None = None,
                  repair: bool = False) -> None:
        now = self._now(now)
        if self.pending:
            if float(sentiment) < 0.0:
                self._record(0.0, result="negative")
                duration = float(cooldown_s if cooldown_s is not None else self.negative_cooldown_s)
                self.cooldown_duration = duration
                self.cooldown_until = now + duration
            else:
                self._record(1.0, result="accepted")
        elif repair and now < self.cooldown_until:
            remaining = max(0.0, self.cooldown_until - now) * 0.35
            self.cooldown_until = now + remaining
        self.last_user = now
        self.queue.clear()

    def force(self, now=None) -> Dict[str, object] | None:
        now = self._now(now)
        feats = self._features(now)
        reply = str(self.generator("", "") or "").strip()
        if not reply:
            return None
        self.queue.append({"reply": reply, "at": now, "features": feats})
        self.pending = {"at": now, "features": feats}
        self.last_proactive = now
        self.sent_today += 1
        self.iterator.record_sent(feats)
        return self.queue[-1]

    def tick(self, now=None) -> str:
        now = self._now(now)
        day = __import__("time").strftime("%Y-%m-%d", __import__("time").localtime(now))
        if day != self.day:
            self.day = day
            self.sent_today = 0
        if self.pending and now - float(self.pending.get("at", now)) >= self.ignore_s:
            self._record(0.0, result="ignored")
            try:
                if self.on_ignored is not None:
                    self.on_ignored({"kind": "proactive", "at": now})
            except Exception:
                pass
            return ""
        if now < self.cooldown_until:
            return ""
        if self.pending or self.queue:
            return ""
        if now - self.last_user < self.quiet_s:
            return ""
        if self.sent_today >= self.max_per_day:
            return ""
        feats = self._features(now)
        decision, prob = self.policy.decide(feats)
        if not decision:
            return ""
        reply = str(self.generator("", "") or "").strip()
        if not reply:
            return ""
        self.queue.append({"reply": reply, "at": now, "features": feats, "prob": round(prob, 3)})
        self.pending = {"at": now, "features": feats}
        self.last_proactive = now
        self.sent_today += 1
        self.iterator.record_sent(feats)
        self.last_generated = reply
        return reply

    def pop(self) -> Dict[str, object] | None:
        if not self.queue:
            return None
        return self.queue.pop(0)

    def feedback(self, reward: float) -> Dict[str, object]:
        r = float(reward)
        self._record(r)
        return {"acceptance": round(self.iterator.acceptance, 3),
                "last_result": self.iterator.last_result,
                "sent": self.iterator.sent}
