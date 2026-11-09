"""Learned proactive-intervention policy (no hand-written decision rules).

Why this module exists
----------------------
The previous proactive path was gated by hand-written rules (quiet hours,
daily caps, cooldowns, fixed thresholds, ignored-times-a-row pause).  That
produced spam and could not adapt: on 2026-10-20 the bot sent 21 unsolicited
private messages after the user explicitly asked it to stop.

Papers this design is based on
------------------------------
* An, T. (2026). When Should the Agent Speak? A Survey of Intervention
  Timing for Always-On AI Assistants (TMLR).  Decision rule: intervene iff
  expected benefit of acting > expected cost of interrupting; the survey
  argues intervention timing should be learned online by randomizing decision
  points against a validated proximal outcome.  Here benefit and cost are
  both learned from observed outcomes; there is no fixed timing heuristic.
* Liu et al. (CHI 25). Proactive Conversational Agents with Inner Thoughts:
  proactivity comes from generated inner thoughts evaluated by an intrinsic
  motivation, not from next-speaker prediction.  The policy scores a latent
  motivation to speak from the agent state and only then generates text.
* Houde et al. (IUI 25). Controlling AI Agent Participation in Group
  Conversations (Koala): imperative decision logic for valuable contribution
  is brittle; users want control over when/what/where the agent responds.
  Here the policy is learned per user from outcome feedback instead of rules.
* Rizk et al. (2020). A Snooze-less User-Aware Notification System for
  Proactive Conversational Agents (arXiv:2003.02097): alert fatigue is a
  learning problem; learn from a wide set of users and customise per user.
  Here a global model seeds every per-user model.
* When2Speak (duke-trust-lab): dataset of speak/silence decisions; local copy
  in mindbase/data/real/When2Speak is the reference task definition.
* Li et al. (2010). LinUCB contextual bandit: linear reward model with an
  upper-confidence exploration term.  This module is a numpy-free variant.

Design
------
For each user we keep a linear reward model theta = A^-1 b and an exploration
term alpha * sqrt(x^T A^-1 x).  The predicted utility of initiating is the
learned expected reward of speaking now in this context; the alternative
action stay silent has utility 0.  The agent initiates only when the learned
upper confidence bound beats 0 (the survey benefit/cost test with both terms
learned).  Every observed outcome updates A and b; a population model is
updated with the same observations and seeds new users.

No keyword lists, quiet-hour rules, daily caps or fixed cooldowns live here.
Time of day, silence length, recent send count, acceptance history,
fear/joy/expectation, social deficit, sentiment, stress and latent impulse are
features; their weights are learned from outcomes.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

FEATURE_NAMES: List[str] = [
    "bias",
    "silence",
    "since_proactive",
    "sends_today",
    "accept_rate",
    "expectation",
    "fear",
    "joy",
    "settle_horizon",
    "social_deficit",
    "unfinished",
    "user_activity",
    "sentiment",
    "reply_latency",
    "valence",
    "stress",
    "hour_sin",
    "hour_cos",
    "impulse",
]

N_FEATURES = len(FEATURE_NAMES)
_DEFAULT_SINCE_PROACTIVE = 11.0 * 60.0


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except Exception:
        return lo
    return lo if x < lo else hi if x > hi else x


def context_features(m: Dict[str, float]) -> List[float]:
    """Turn raw context metrics into the policy feature vector.

    Log/linear scalings are feature engineering, not decision rules: every
    weight that multiplies them is learned online.
    """
    hour = float(m.get("hour", 0.0))
    return [
        1.0,
        min(math.log1p(max(0.0, float(m.get("silence_sec", 0.0)))) / 8.0, 1.0),
        min(math.log1p(max(0.0, float(m.get("since_proactive_sec", _DEFAULT_SINCE_PROACTIVE)))) / 11.0, 1.0),
        min(max(0.0, float(m.get("sends_today", 0.0))) / 5.0, 1.0),
        _clamp(m.get("accept_rate", 0.5), 0.0, 1.0),
        _clamp(m.get("expectation", 0.5), 0.0, 1.0),
        _clamp(m.get("fear", 0.0), 0.0, 1.0),
        _clamp(m.get("joy", 0.0), 0.0, 1.0),
        min(math.log1p(max(0.0, float(m.get("settle_after", 300.0)))) / 8.0, 1.0),
        _clamp(m.get("social_deficit", 0.0), 0.0, 1.0),
        _clamp(m.get("unfinished", 0.0), 0.0, 1.0),
        min(max(0.0, float(m.get("user_msgs_today", 0.0))) / 10.0, 1.0),
        _clamp(m.get("sentiment", 0.0), -1.0, 1.0),
        min(math.log1p(max(0.0, float(m.get("delay_mean", 300.0)))) / 8.0, 1.0),
        _clamp(m.get("valence", 0.0), -1.0, 1.0),
        _clamp(m.get("stress", 0.0), 0.0, 1.0),
        math.sin(2.0 * math.pi * (hour % 24.0) / 24.0),
        math.cos(2.0 * math.pi * (hour % 24.0) / 24.0),
        _clamp(m.get("impulse", 0.0), 0.0, 1.0),
    ]


def _matvec(a: Sequence[Sequence[float]], x: Sequence[float]) -> List[float]:
    return [sum(a[i][j] * x[j] for j in range(len(x))) for i in range(len(a))]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _inverse(matrix: Sequence[Sequence[float]]) -> List[List[float]]:
    """Gauss-Jordan inverse with partial pivoting (numpy-free, n=19)."""
    n = len(matrix)
    a = [[float(v) for v in row] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            a[pivot][col] = 1e-12
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        pv = a[col][col]
        a[col] = [v / pv for v in a[col]]
        for r in range(n):
            if r == col:
                continue
            f = a[r][col]
            if f == 0.0:
                continue
            a[r] = [a[r][j] - f * a[col][j] for j in range(2 * n)]
    return [row[n:] for row in a]


class ProactivePolicy:
    """Per-user online-learned proactive timing policy (LinUCB-style)."""

    def __init__(self, root: Path | str, user_id: str, prior_reward: float = -0.5,
                 alpha: float = 0.20, ridge: float = 1.0, log_path: Path | str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.user_id = str(user_id)
        self.alpha = float(alpha)
        self.ridge = float(ridge)
        self.log_path = Path(log_path) if log_path else None
        self._lock = threading.RLock()
        self._Ainv: Optional[List[List[float]]] = None
        self._load(prior_reward)

    # ---------------------------------------------------------------- storage
    def _path(self) -> Path:
        key = hashlib.sha256(self.user_id.encode("utf-8")).hexdigest()[:16]
        return self.root / (key + ".json")

    def _global_path(self) -> Path:
        return self.root / "_global.json"

    def _blank(self, prior_reward: float) -> Dict[str, object]:
        A = [[self.ridge if i == j else 0.0 for j in range(N_FEATURES)] for i in range(N_FEATURES)]
        b = [0.0] * N_FEATURES
        b[0] = float(prior_reward)
        return {
            "user_id": self.user_id, "A": A, "b": b, "alpha": self.alpha,
            "n": 0, "sent": 0, "accepted": 0, "ignored": 0, "reward_sum": 0.0,
            "last_send_ts": 0.0, "send_times": [], "user_msgs_date": "",
            "user_msgs_today": 0, "sentiment_ema": 0.0, "pending": None,
            "recent_texts": [], "last_update": 0.0,
        }

    def _load(self, prior_reward: float) -> None:
        path = self._path()
        data = None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if not isinstance(data, dict):
            data = self._blank(prior_reward)
            gp = self._global_path()
            if gp.exists():
                try:
                    g = json.loads(gp.read_text(encoding="utf-8"))
                    if isinstance(g.get("A"), list) and isinstance(g.get("b"), list) and len(g["b"]) == N_FEATURES:
                        data["A"] = [list(row) for row in g["A"]]
                        data["b"] = list(g["b"])
                        data["b"][0] = float(prior_reward)
                except Exception:
                    pass
        base = self._blank(prior_reward)
        for k, v in data.items():
            if k in ("A", "b"):
                continue
            base[k] = v
        base["A"] = data.get("A") or base["A"]
        base["b"] = data.get("b") or base["b"]
        self.data: Dict[str, object] = base
        self._Ainv = None

    def save(self) -> None:
        with self._lock:
            try:
                self._path().write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

    def _update_global(self, x: Sequence[float], reward: float) -> None:
        gp = self._global_path()
        try:
            current = json.loads(gp.read_text(encoding="utf-8")) if gp.exists() else None
        except Exception:
            current = None
        if not isinstance(current, dict) or not isinstance(current.get("A"), list):
            current = {"A": [[self.ridge if i == j else 0.0 for j in range(N_FEATURES)] for i in range(N_FEATURES)],
                       "b": [0.0] * N_FEATURES, "n": 0}
        try:
            A = current["A"]
            b = current["b"]
            for i in range(N_FEATURES):
                xi = float(x[i])
                b[i] = float(b[i]) + reward * xi
                row = A[i]
                for j in range(N_FEATURES):
                    row[j] = float(row[j]) + xi * float(x[j])
            current["n"] = int(current.get("n", 0)) + 1
            gp.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # -------------------------------------------------------------- utilities
    def sends_today(self, now: float) -> int:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        return sum(1 for t in self.data.get("send_times", [])
                   if time.strftime("%Y-%m-%d", time.localtime(float(t))) == day)

    def user_msgs_today(self, now: float) -> int:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if str(self.data.get("user_msgs_date", "")) != day:
            return 0
        return int(self.data.get("user_msgs_today", 0))

    @property
    def sentiment_ema(self) -> float:
        return float(self.data.get("sentiment_ema", 0.0))

    @property
    def pending(self) -> Optional[Dict[str, object]]:
        return self.data.get("pending") if isinstance(self.data.get("pending"), dict) else None

    @property
    def last_send_ts(self) -> float:
        return float(self.data.get("last_send_ts", 0.0))

    @property
    def recent_texts(self) -> List[str]:
        return [str(x) for x in self.data.get("recent_texts", [])][-5:]

    def _theta(self) -> List[float]:
        if self._Ainv is None:
            self._Ainv = _inverse(self.data["A"])
        return _matvec(self._Ainv, self.data["b"])

    def expected_value(self, x: Sequence[float]) -> float:
        """Learned expected reward of initiating in this context."""
        with self._lock:
            return float(_dot(self._theta(), x))

    def score(self, x: Sequence[float]) -> Dict[str, float]:
        """Learned upper confidence bound used for the initiate / stay-silent decision."""
        with self._lock:
            if self._Ainv is None:
                self._Ainv = _inverse(self.data["A"])
            Ainv = self._Ainv
            theta = _matvec(Ainv, self.data["b"])
            mean = _dot(theta, x)
            var = _dot(x, _matvec(Ainv, x))
            bonus = float(self.alpha) * math.sqrt(max(0.0, float(var)))
            return {"mean": float(mean), "bonus": float(bonus), "score": float(mean + bonus)}

    def should_initiate(self, x: Sequence[float]) -> Dict[str, object]:
        s = self.score(x)
        return {"decide": bool(s["score"] > 0.0), **s}

    # ---------------------------------------------------------------- learning
    def learn(self, x: Sequence[float], reward: float) -> Dict[str, float]:
        """Online update from one observed outcome (user model + population prior)."""
        reward = _clamp(reward, -2.0, 1.0)
        with self._lock:
            A = self.data["A"]
            b = self.data["b"]
            for i in range(N_FEATURES):
                xi = float(x[i])
                b[i] = float(b[i]) + reward * xi
                row = A[i]
                for j in range(N_FEATURES):
                    row[j] = float(row[j]) + xi * float(x[j])
            self.data["n"] = int(self.data.get("n", 0)) + 1
            self.data["reward_sum"] = float(self.data.get("reward_sum", 0.0)) + reward
            if reward > 0.0:
                self.data["accepted"] = int(self.data.get("accepted", 0)) + 1
            elif reward < 0.0:
                self.data["ignored"] = int(self.data.get("ignored", 0)) + 1
            self._Ainv = None
            self.data["last_update"] = time.time()
            self.save()
            self._update_global(x, reward)
            self._log({"event": "learn", "ts": time.time(), "reward": reward,
                       "n": int(self.data["n"]), "features": [float(v) for v in x]})
            return {"reward": reward, "n": int(self.data["n"])}

    def note_user_message(self, sentiment: float, now: float) -> Dict[str, float]:
        """Record a user-initiated message (engagement outcome) and update the EMA feature."""
        sentiment = _clamp(sentiment, -1.0, 1.0)
        with self._lock:
            ema = float(self.data.get("sentiment_ema", 0.0))
            self.data["sentiment_ema"] = 0.7 * ema + 0.3 * sentiment
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            if str(self.data.get("user_msgs_date", "")) != day:
                self.data["user_msgs_date"] = day
                self.data["user_msgs_today"] = 0
            self.data["user_msgs_today"] = int(self.data.get("user_msgs_today", 0)) + 1
            self.save()
            return {"sentiment_ema": float(self.data.get("sentiment_ema", 0.0))}

    def record_send(self, x: Sequence[float], text: str, now: float, hour: int, topic: str = "") -> None:
        with self._lock:
            self.data["pending"] = {"x": [float(v) for v in x], "text": str(text), "ts": float(now),
                                    "hour": int(hour), "topic": str(topic)}
            self.data["last_send_ts"] = float(now)
            times = [float(t) for t in self.data.get("send_times", [])][-99:]
            times.append(float(now))
            self.data["send_times"] = times
            self.data["sent"] = int(self.data.get("sent", 0)) + 1
            recent = [str(t) for t in self.data.get("recent_texts", [])][-4:]
            recent.append(str(text)[:200])
            self.data["recent_texts"] = recent
            self.save()
            self._log({"event": "send", "ts": float(now), "text": str(text), "hour": int(hour),
                       "topic": str(topic), "score": self.score(x)["score"],
                       "features": [float(v) for v in x]})

    def settle_timeout(self, now: float, horizon: float) -> Optional[Dict[str, object]]:
        """If the pending initiative passed the learned wait horizon, treat it as ignored."""
        with self._lock:
            pending = self.pending
            if pending is None:
                return None
            age = float(now) - float(pending.get("ts", now))
            if horizon > 0 and age < float(horizon):
                return None
            x = pending.get("x") or []
            self.data["pending"] = None
            reward = -1.0
            self.learn(x, reward)
            out = {"reason": "timeout", "reward": reward, "age": age, "delay": age,
                   "hour": int(pending.get("hour", 0)), "topic": str(pending.get("topic", "")),
                   "text": str(pending.get("text", ""))}
            self._log({"event": "outcome", **out})
            return out

    def settle_reply(self, sentiment: float, now: float, horizon: float) -> Optional[Dict[str, object]]:
        """Settle a pending initiative with the user reply."""
        with self._lock:
            pending = self.pending
            if pending is None:
                return None
            age = float(now) - float(pending.get("ts", now))
            x = pending.get("x") or []
            self.data["pending"] = None
            s = _clamp(sentiment, -1.0, 1.0)
            if horizon > 0 and age > float(horizon):
                reward = s - 0.5
            else:
                reward = s + 0.5
            reward = _clamp(reward, -2.0, 1.0)
            self.learn(x, reward)
            out = {"reason": "reply", "reward": reward, "age": age, "delay": age,
                   "hour": int(pending.get("hour", 0)), "topic": str(pending.get("topic", "")),
                   "text": str(pending.get("text", "")), "sentiment": s}
            self._log({"event": "outcome", **out})
            return out

    # ------------------------------------------------------------------ misc
    def _log(self, row: Dict[str, object]) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"user": self.user_id, **row}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def summary(self) -> Dict[str, object]:
        return {"n": int(self.data.get("n", 0)), "sent": int(self.data.get("sent", 0)),
                "accepted": int(self.data.get("accepted", 0)), "ignored": int(self.data.get("ignored", 0)),
                "reward_sum": round(float(self.data.get("reward_sum", 0.0)), 4),
                "alpha": self.alpha, "pending": bool(self.pending)}

    def stats(self, now: float) -> Dict[str, object]:
        return {"sent": int(self.data.get("sent", 0)), "accepted": int(self.data.get("accepted", 0)),
                "ignored": int(self.data.get("ignored", 0)), "delay_sum": 0.0,
                "policy_n": int(self.data.get("n", 0))}
