from __future__ import annotations

from typing import Dict, List, Sequence

from ..noise import clip
from .dataset import ACTION_LABELS, StorySample
from .features import sample_vector
from .linear import LinearRegressor

NEXT_KEYS = ("stress", "social", "mood", "energy", "identity", "reward")


def _one_hot(action: str) -> List[float]:
    return [1.0 if action == a else 0.0 for a in ACTION_LABELS]


class WorldModel:
    def __init__(self, n_features: int, lr: float = 0.03):
        self.n_features = n_features + len(ACTION_LABELS)
        self.reg = LinearRegressor(self.n_features, len(NEXT_KEYS), lr=lr)

    def _vector(self, event: str, state: Dict[str, float], action: str, structured=None) -> List[float]:
        return sample_vector(event, state, structured) + _one_hot(action)

    def fit(self, samples: Sequence[StorySample], epochs: int = 200) -> "WorldModel":
        X = [self._vector(s.event, s.state, s.action, getattr(s, 'structured', None)) for s in samples]
        Y = [[float(s.outcome.get(k, 0.0)) for k in NEXT_KEYS[:-1]] + [float(s.reward)] for s in samples]
        self.reg.fit(X, Y, epochs=epochs)
        return self

    def predict(self, event: str, state: Dict[str, float], action: str, structured=None) -> Dict[str, float]:
        raw = self.reg.predict(self._vector(event, state, action, structured))
        out = dict(zip(NEXT_KEYS, raw))
        return {
            "stress": round(clip(out["stress"], 0.0, 1.0), 4),
            "social": round(clip(out["social"], 0.0, 1.0), 4),
            "mood": round(clip(out["mood"], -1.0, 1.0), 4),
            "energy": round(clip(out["energy"], 0.0, 1.0), 4),
            "identity": round(clip(out["identity"], 0.0, 1.0), 4),
            "reward": round(out["reward"], 4),
        }

    def loss(self, samples: Sequence[StorySample]) -> float:
        X = [self._vector(s.event, s.state, s.action) for s in samples]
        Y = [[float(s.outcome.get(k, 0.0)) for k in NEXT_KEYS[:-1]] + [float(s.reward)] for s in samples]
        return round(self.reg.loss(X, Y), 4)
