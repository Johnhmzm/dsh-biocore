from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence

from .dataset import StorySample
from .features import feature_names, sample_vector
from .linear import SoftmaxRegression

DEFAULT = Path(__file__).resolve().parent.parent / "data" / "decision_policy.json"
LABELS = ("speak", "silence")


class DecisionModel:
    """Learned speak/silence head. Replaces _decide heuristics when installed."""

    def __init__(self, clf: SoftmaxRegression):
        self.clf = clf
        self.threshold = 0.0

    @classmethod
    def fit(cls, samples: Sequence[StorySample], epochs: int = 150) -> "DecisionModel":
        rows = [(s, s.action) for s in samples if s.action in LABELS]
        clf = SoftmaxRegression(len(feature_names()) + 3, len(LABELS), lr=0.10)
        X = [sample_vector(s.event, s.state, getattr(s, "structured", None)) + [0.0, 0.0, 0.0] for s, _ in rows]
        y = [LABELS.index(a) for _, a in rows]
        clf.fit(X, y, epochs=epochs)
        return cls(clf)

    def predict_proba(
        self,
        event: str,
        state: Dict[str, float],
        directed: bool = False,
        asleep: bool = False,
        kind: str = "message",
    ) -> Dict[str, float]:
        vec = sample_vector(event, state or {}) + [
            1.0 if directed else 0.0,
            1.0 if asleep else 0.0,
            1.0 if kind == "tick" else 0.0,
        ]
        p = self.clf.predict_proba(vec)
        return {k: float(v) for k, v in zip(LABELS, p)}

    def accuracy(self, samples: Sequence[StorySample]) -> float:
        rows = [(s, s.action) for s in samples if s.action in LABELS]
        if not rows:
            return 0.0
        X = [sample_vector(s.event, s.state, getattr(s, "structured", None)) + [0.0, 0.0, 0.0] for s, _ in rows]
        y = [LABELS.index(a) for _, a in rows]
        return round(self.clf.accuracy(X, y), 4)

    def save(self, path: Path | str | None = None) -> None:
        Path(path or DEFAULT).write_text(json.dumps({"clf": self.clf.to_dict(), "labels": LABELS}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str | None = None) -> "DecisionModel":
        d = json.loads(Path(path or DEFAULT).read_text(encoding="utf-8"))
        return cls(SoftmaxRegression.from_dict(d["clf"]))


def distill(limit: int = 1000, seed: int = 42, holdout: float = 0.2) -> dict:
    from ..config import MindConfig
    from ..engine import MindBase
    from ..types import Event
    from .behavior import load_annotations

    X, y = [], []
    mb = MindBase(MindConfig(seed=seed))
    for s in load_annotations(limit=limit):
        state = mb._state_features()
        base = sample_vector(s.event, state) + [0.0, 0.0, 0.0]
        # normal / directed / tick / asleep
        for flags, ev in (
            ([0.0, 0.0, 0.0], Event(text=s.event, speaker="A", directed=False, ts=mb.t + 60)),
            ([1.0, 0.0, 0.0], Event(text=s.event, speaker="A", directed=True, ts=mb.t + 60)),
            ([0.0, 0.0, 1.0], Event(kind="tick", ts=mb.t + 60)),
        ):
            out = mb.step(ev)
            X.append(base + flags)
            y.append(LABELS.index(out.action))
        # asleep path (force nighttime + high sleep pressure)
        old_t, old_p = mb.clock.t, mb.clock.sleep_pressure
        mb.clock.t = 0.95 * 86400.0
        mb.clock.sleep_pressure = 1.0
        out = mb.step(Event(text=s.event, speaker="A", directed=False, ts=mb.t + 60))
        mb.clock.t, mb.clock.sleep_pressure = old_t, old_p
        X.append(base + [0.0, 1.0, 0.0])
        y.append(LABELS.index(out.action))

    cut = max(1, int(len(X) * (1.0 - holdout)))
    clf = SoftmaxRegression(len(feature_names()) + 3, len(LABELS), lr=0.10)
    clf.fit(X[:cut], y[:cut], epochs=150)
    model = DecisionModel(clf)
    model.save()
    return {
        "n": len(X),
        "train_n": cut,
        "test_n": len(X) - cut,
        "train_accuracy": round(clf.accuracy(X[:cut], y[:cut]), 4),
        "test_accuracy": round(clf.accuracy(X[cut:], y[cut:]), 4),
    }


if __name__ == "__main__":
    print(distill())
