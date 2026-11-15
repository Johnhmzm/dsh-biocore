from __future__ import annotations

from typing import List, Sequence

from ..actions import ACTION_KINDS
from .behavior_episode import load_episode
from .dataset import StorySample
from .linear import LinearRegressor

KEYS = ("stress", "social", "mood", "energy", "identity")


def vector(s: StorySample) -> List[float]:
    return [float(s.state.get(k, 0.0)) for k in KEYS] + [1.0 if s.action_kind == k else 0.0 for k in ACTION_KINDS]


class BehaviorWorldModel:
    def __init__(self):
        self.reg = LinearRegressor(len(KEYS) + len(ACTION_KINDS), len(KEYS) + 1)

    def fit(self, samples: Sequence[StorySample], epochs: int = 100) -> "BehaviorWorldModel":
        X = [vector(s) for s in samples]
        Y = [[float(s.outcome.get(k, 0.0)) for k in KEYS] + [float(s.reward)] for s in samples]
        self.reg.fit(X, Y, epochs=epochs)
        return self

    def predict(self, sample: StorySample) -> dict:
        raw = self.reg.predict(vector(sample))
        out = dict(zip(KEYS + ("reward",), raw))
        return {k: round(float(v), 4) for k, v in out.items()}

    def mae(self, samples: Sequence[StorySample]) -> dict:
        if not samples:
            return {}
        errs = {k: 0.0 for k in KEYS + ("reward",)}
        for s in samples:
            p = self.predict(s)
            true = {k: float(s.outcome.get(k, 0.0)) for k in KEYS}
            true["reward"] = float(s.reward)
            for k in errs:
                errs[k] += abs(p[k] - true[k])
        return {f"mae_{k}": round(v / len(samples), 4) for k, v in errs.items()}


def train_episode(epochs: int = 100) -> dict:
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR"
    rows = load_episode(root / "episode_00000010.parquet", root / "annotations" / "task-0000" / "episode_00000010.json")
    cut = int(len(rows) * 0.8)
    model = BehaviorWorldModel().fit(rows[:cut], epochs=epochs)
    return {"n": len(rows), "train": model.mae(rows[:cut]), "test": model.mae(rows[cut:])}


if __name__ == "__main__":
    print(train_episode())
