from __future__ import annotations

import random
from typing import Dict, List

import numpy as np

from .noise import clip

GOALS = ("恢复能量", "连接别人", "寻找安全", "探索新东西", "理解现在的状态")


class GoalGenerator:
    """Learned goal selection from drive/error features; updated by outcome RPE."""

    def __init__(self, seed: int = 1):
        self.rng = np.random.default_rng(seed)
        self.w = self.rng.normal(0, 0.05, (len(GOALS), 5))
        self.lr = 0.05
        self.baseline = 0.0
        self.baseline_lr = 0.10
        self.value_w = np.zeros(5)
        self.value_lr = 0.05
        self.g2 = np.full(5, 1e-6)

    def features(self, drives, criticality: float = 0.0) -> np.ndarray:
        return np.array([
            abs(float(drives.errors.get("energy", 0.0))),
            abs(float(drives.errors.get("social", 0.0))),
            abs(float(drives.errors.get("safety", 0.0))),
            abs(float(drives.errors.get("curiosity", 0.0))),
            abs(float(criticality)),
        ])

    def choose(self, drives, criticality: float = 0.0) -> Dict[str, object]:
        x = self.features(drives, criticality)
        scores = self.w @ x
        z = scores / 0.5
        z = z - np.max(z)
        p = np.exp(z)
        p = p / np.sum(p)
        idx = int(self.rng.choice(len(GOALS), p=p))
        return {"index": idx, "goal": GOALS[idx], "scores": [round(float(s), 4) for s in scores], "probs": [round(float(v), 3) for v in p]}

    def value(self, features: np.ndarray) -> float:
        return float(self.value_w @ features)

    def learn(self, index: int, outcome: float, features: np.ndarray) -> None:
        predicted = self.value(features)
        td = float(outcome) - predicted
        self.last_td = td
        grad = td * features
        self.g2 = 0.90 * self.g2 + 0.10 * (grad * grad)
        self.value_w = self.value_w + self.value_lr * grad / (np.sqrt(self.g2) + 1e-8)
        advantage = td
        self.w[int(index)] = self.w[int(index)] + self.lr * advantage * features
        self.baseline = self.baseline + self.baseline_lr * advantage

    def snapshot(self) -> dict:
        return {
            "baseline": round(float(self.baseline), 4),
            "weights": [round(float(x), 4) for x in self.w[:, 0]],
            "value_weights": [round(float(x), 4) for x in self.value_w],
            "last_td": round(float(getattr(self, "last_td", 0.0)), 4),
        }


ACTIONS = ("approach", "explore", "avoid", "rest")


class ActionCritic:
    def __init__(self, seed: int = 1):
        self.rng = np.random.default_rng(seed)
        self.w = np.zeros((len(ACTIONS), 5))
        self.g2 = np.full((len(ACTIONS), 5), 1e-6)
        self.lr = 0.10
        self.epsilon = 0.10
        self.policy_w = np.zeros((len(ACTIONS), 5))

    def probs(self, features: np.ndarray) -> np.ndarray:
        z = (self.w + self.policy_w) @ features
        z = z - np.max(z)
        p = np.exp(z)
        return p / np.sum(p)

    def choose(self, features: np.ndarray, greedy: bool = False) -> str:
        if greedy:
            return ACTIONS[int(np.argmax(self.w @ features))]
        return ACTIONS[int(self.rng.choice(len(ACTIONS), p=self.probs(features)))]

    def learn(self, action: str, outcome: float, features: np.ndarray) -> None:
        a = ACTIONS.index(action)
        q = float(self.w[a] @ features)
        td = float(outcome) - q
        grad = td * features
        self.g2[a] = 0.90 * self.g2[a] + 0.10 * (grad * grad)
        self.w[a] = self.w[a] + self.lr * grad / (np.sqrt(self.g2[a]) + 1e-8)
        self.policy_w[a] = self.policy_w[a] + 0.05 * td * features
