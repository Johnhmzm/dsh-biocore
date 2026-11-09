"""DSH-orchestrated companion training loop.

DSH is the teacher/orchestrator.  The thing being trained is a small affect
policy (empathy gain), not the LLM weights.  A real LLM judge can replace the
scripted teacher later.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Dict, List

from .config import MindConfig
from .engine import MindBase


USER_EMOTIONS = ("sadness", "anxiety", "loneliness", "joy", "anger", "calm")
NEGATIVE = frozenset(("sadness", "anxiety", "loneliness", "anger"))


@dataclass
class Episode:
    emotion: str
    intensity: float
    gain: float
    response: str
    reward: float
    state: Dict[str, float]


class ScriptedTeacher:
    """Cheap teacher used until a real DSH/LLM judge is attached."""

    RESPONSES = {
        "sadness": ("我在，不用一个人扛。", "听起来真的很难受，我陪你一会儿。"),
        "anxiety": ("先别急，我们一件一件来。", "我在听，你慢慢说。"),
        "loneliness": ("我在呢，你不是一个人。", "想说话的时候我都在。"),
        "joy": ("听起来真好，我也替你开心。", "这个值得记下来。"),
        "anger": ("这确实让人生气，我先听你说。", "不用憋着，我在。"),
        "calm": ("这样挺好，慢慢来。", "我在，陪你待一会儿。"),
    }

    def respond(self, emotion: str, rng: random.Random) -> str:
        return rng.choice(self.RESPONSES.get(emotion, ("我在。",)))

    def judge(self, emotion: str, response: str, state: Dict[str, float]) -> float:
        score = 0.0
        if emotion in NEGATIVE:
            if state.get("v", 0.0) <= 0.15:
                score += 0.35
            if state.get("stress", 0.0) >= 0.2:
                score += 0.20
            if any(w in response for w in ("我在", "陪", "听", "不用")):
                score += 0.30
        else:
            if state.get("v", 0.0) >= -0.1:
                score += 0.40
            if any(w in response for w in ("开心", "好", "记")):
                score += 0.30
        # too much mirroring is not always good
        if state.get("v", 0.0) < -0.9 or state.get("stress", 0.0) > 0.9:
            score -= 0.20
        return max(0.0, min(1.0, round(score, 4)))


class EmpathyBandit:
    def __init__(self, arms=(0.15, 0.30, 0.45, 0.60, 0.75), epsilon: float = 0.25, rng=None):
        self.arms = tuple(float(x) for x in arms)
        self.epsilon = float(epsilon)
        self.rng = rng or random.Random(1)
        self.q = {a: 0.0 for a in self.arms}
        self.n = {a: 0 for a in self.arms}

    def choose(self) -> float:
        if self.rng.random() < self.epsilon:
            return self.rng.choice(self.arms)
        return max(self.arms, key=lambda a: (self.q[a], -self.n[a]))

    def update(self, arm: float, reward: float) -> None:
        self.n[arm] += 1
        self.q[arm] += (reward - self.q[arm]) / self.n[arm]

    def best(self) -> float:
        return max(self.arms, key=lambda a: self.q[a])


class DSHCompanionTrainer:
    def __init__(self, seed: int = 1, teacher=None):
        self.rng = random.Random(int(seed))
        self.teacher = teacher or ScriptedTeacher()
        self.bandit = EmpathyBandit(rng=self.rng)

    def run_episode(self, emotion: str, intensity: float, gain: float) -> Episode:
        mb = MindBase(MindConfig(seed=self.rng.randint(1, 10**6)))
        mb.empathize(emotion, intensity, empathy_gain=gain)
        response = self.teacher.respond(emotion, self.rng)
        snap = mb.snapshot()
        reward = self.teacher.judge(emotion, response, snap.get("affect", {}))
        return Episode(emotion, intensity, gain, response, reward, snap.get("affect", {}))

    def train(self, episodes: int = 60) -> Dict[str, object]:
        rows: List[Episode] = []
        for _ in range(int(episodes)):
            emotion = self.rng.choice(USER_EMOTIONS)
            intensity = round(self.rng.uniform(0.4, 0.9), 3)
            arm = self.bandit.choose()
            ep = self.run_episode(emotion, intensity, arm)
            self.bandit.update(arm, ep.reward)
            rows.append(ep)
        best = self.bandit.best()
        return {
            "episodes": len(rows),
            "best_gain": best,
            "q": {str(k): round(v, 4) for k, v in self.bandit.q.items()},
            "n": {str(k): int(v) for k, v in self.bandit.n.items()},
            "mean_reward": round(sum(r.reward for r in rows) / max(1, len(rows)), 4),
            "best_reward": round(max(r.reward for r in rows), 4),
            "last": rows[-5:],
        }


def save_result(result: Dict[str, object], path: str = "mindbase/data/dsh_companion_train.json") -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1, default=str)
    return path
