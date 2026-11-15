from __future__ import annotations

from typing import Dict, List, Sequence

from ..noise import clip
from .dataset import StorySample
from .features import sample_vector
from .linear import LinearRegressor, RidgeRegressor

TARGETS = ("valence", "arousal", "threat", "social", "relevance")
TARGETS_VAD = ("valence", "arousal", "dominance")


class AppraisalModel:
    def __init__(self, n_features: int, lr: float = 0.05, ridge: bool = False, alpha: float = 1.0,
                 targets: Sequence[str] = TARGETS):
        self.targets = tuple(targets)
        self.reg = RidgeRegressor(n_features, len(self.targets), lr=lr, alpha=alpha) if ridge else LinearRegressor(n_features, len(self.targets), lr=lr)

    def fit(self, samples: Sequence[StorySample], epochs: int = 200) -> "AppraisalModel":
        X = [sample_vector(s.event, s.state, getattr(s, 'structured', None)) for s in samples]
        Y = [[float(s.appraisal.get(k, 0.0)) for k in self.targets] for s in samples]
        self.reg.fit(X, Y, epochs=epochs)
        return self

    def predict(self, event: str, state: Dict[str, float], structured=None) -> Dict[str, float]:
        x = sample_vector(event, state, structured)
        n = int(getattr(self.reg, "n_features", len(x)))
        if len(x) > n:
            x = x[:n]
        elif len(x) < n:
            x = list(x) + [0.0] * (n - len(x))
        out = dict(zip(self.targets, self.reg.predict(x)))
        return {k: round(clip(v, 0.0 if k != "valence" else -1.0, 1.0), 4) for k, v in out.items()}

    def loss(self, samples: Sequence[StorySample]) -> float:
        X = [sample_vector(s.event, s.state, getattr(s, 'structured', None)) for s in samples]
        Y = [[float(s.appraisal.get(k, 0.0)) for k in self.targets] for s in samples]
        return round(self.reg.loss(X, Y), 4)


class CompositeAppraisalModel:
    """Route V/A from an EmoBank head and threat/social/relevance from chat data."""

    def __init__(self, va: AppraisalModel, chat: AppraisalModel):
        self.va = va
        self.chat = chat

    def predict(self, event: str, state: Dict[str, float], structured=None) -> Dict[str, float]:
        a = self.va.predict(event, state, structured)
        b = self.chat.predict(event, state, structured)
        return {
            "valence": a.get("valence", 0.0),
            "arousal": a.get("arousal", 0.5),
            "threat": b.get("threat", 0.0),
            "social": b.get("social", 0.5),
            "relevance": b.get("relevance", 0.5),
        }

    def to_dict(self) -> Dict[str, object]:
        return {
            "va": {"n_features": self.va.reg.n_features, "state": self.va.reg.to_dict()},
            "chat": {"n_features": self.chat.reg.n_features, "state": self.chat.reg.to_dict()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "CompositeAppraisalModel":
        def make_reg(state):
            state = state or {}
            if state.get("kind") == "ridge":
                return RidgeRegressor.from_dict(state)
            return LinearRegressor.from_dict(state)
        va_state = data.get("va") or {}
        chat_state = data.get("chat") or {}
        va = AppraisalModel(int(va_state.get("n_features") or 31))
        va.reg = make_reg(va_state.get("state"))
        chat = AppraisalModel(int(chat_state.get("n_features") or 31))
        chat.reg = make_reg(chat_state.get("state"))
        return cls(va, chat)
