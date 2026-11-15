from __future__ import annotations

from typing import Dict, List, Sequence

from .dataset import ACTION_LABELS, StorySample
from .features import sample_vector
from .linear import SoftmaxRegression


class PolicyModel:
    def __init__(self, n_features: int, lr: float = 0.15):
        self.clf = SoftmaxRegression(n_features, len(ACTION_LABELS), lr=lr)

    def fit(self, samples: Sequence[StorySample], epochs: int = 200) -> "PolicyModel":
        X = [sample_vector(s.event, s.state, getattr(s, 'structured', None)) for s in samples]
        y = [ACTION_LABELS.index(s.action) for s in samples if s.action in ACTION_LABELS]
        X = [x for x, s in zip(X, samples) if s.action in ACTION_LABELS]
        self.clf.fit(X, y, epochs=epochs)
        return self

    def predict(self, event: str, state: Dict[str, float], structured=None) -> str:
        return ACTION_LABELS[self.clf.predict(sample_vector(event, state, structured))]

    def predict_proba(self, event: str, state: Dict[str, float], structured=None) -> Dict[str, float]:
        p = self.clf.predict_proba(sample_vector(event, state, structured))
        return {k: round(v, 4) for k, v in zip(ACTION_LABELS, p)}

    def accuracy(self, samples: Sequence[StorySample]) -> float:
        X = [sample_vector(s.event, s.state, getattr(s, 'structured', None)) for s in samples if s.action in ACTION_LABELS]
        y = [ACTION_LABELS.index(s.action) for s in samples if s.action in ACTION_LABELS]
        return round(self.clf.accuracy(X, y), 3)
