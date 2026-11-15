"""Discrete-time survival model for 1v1 timing.

Tool transfer: survival analysis was invented for time-to-event data with
censoring; "did the bot respond?" is exactly a time-to-response problem.
Instead of binary speak/silence classification, we model the hazard of
responding at each horizon, then derive survival / response probabilities.

No LLM.  Small local tabular model.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .linear import SoftmaxRegression
from .timing import _NEUTRAL_STATE, timing_features

HORIZONS = ("speak_180s", "speak_600s", "speak_1800s", "speak_5turn", "speak_10turn")
N_STAGES = len(HORIZONS)


class DiscreteTimeHazard:
    """Person-period logistic hazard model: P(event at stage k | survived to k)."""

    def __init__(self, n_features: int = 32, lr: float = 0.05):
        self.clf = SoftmaxRegression(int(n_features), 2, lr=float(lr))

    def _features(self, context: str, state=None, stage: int = 0) -> List[float]:
        base = timing_features(context, state, include_state=True)
        stage = max(0, min(N_STAGES - 1, int(stage)))
        return base + [1.0 if stage == k else 0.0 for k in range(N_STAGES)]

    def fit(self, records: Sequence[Sequence[object]], epochs: int = 100) -> "DiscreteTimeHazard":
        X: List[List[float]] = []
        y: List[int] = []
        for item in records:
            context = str(item[0])
            flags = [int(v) for v in (item[1] or [])]
            state = item[2] if len(item) > 2 else None
            for k in range(N_STAGES):
                flag = flags[k] if k < len(flags) else 0
                X.append(self._features(context, state, k))
                y.append(1 if flag else 0)
                if flag:
                    break
        self.clf.fit(X, y, epochs=int(epochs))
        return self

    def predict_hazard(self, context: str, state=None, stage: int = 0) -> float:
        p = float(self.clf.predict_proba(self._features(context, state, stage))[1])
        return max(0.0, min(1.0, p))

    def predict_survival(self, context: str, state=None) -> List[float]:
        surv = []
        s = 1.0
        for k in range(N_STAGES):
            h = self.predict_hazard(context, state, k)
            s *= (1.0 - h)
            surv.append(round(s, 5))
        return surv

    def predict_response_by(self, context: str, state=None) -> List[float]:
        return [round(1.0 - s, 5) for s in self.predict_survival(context, state)]

    def to_dict(self) -> Dict[str, object]:
        return {"kind": "survival", "clf": self.clf.to_dict()}

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "DiscreteTimeHazard":
        clf = SoftmaxRegression.from_dict(data.get("clf", data))  # type: ignore[arg-type]
        obj = cls(clf.n_features, clf.lr)
        obj.clf = clf
        return obj


def load_human_thread_survival(path: Path | str) -> List[Tuple[str, List[int], Dict[str, float]]]:
    records = []
    p = Path(path)
    if not p.exists():
        return records
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            flags = []
            prev = 0
            for h in HORIZONS:
                try:
                    v = int(row.get(h, 0)) or 0
                except Exception:
                    v = 0
                v = 1 if v else 0
                prev = max(prev, v)
                flags.append(prev)
            records.append((text, flags, dict(_NEUTRAL_STATE)))
    return records


def evaluate_survival(records, test_ratio: float = 0.25, seed: int = 1) -> Dict[str, object]:
    idx = list(range(len(records)))
    random.Random(int(seed)).shuffle(idx)
    n_test = max(1, int(round(len(idx) * float(test_ratio)))) if idx else 0
    test = [records[i] for i in idx[:n_test]]
    train = [records[i] for i in idx[n_test:]]
    model = DiscreteTimeHazard().fit(train, epochs=120)
    rows = []
    for ctx, flags, state in test:
        pred = model.predict_response_by(ctx, state)
        rows.append({"flags": flags, "pred": pred})
    brier = {}
    for k in range(len(HORIZONS)):
        vals = []
        for r in rows:
            y = 1.0 if r["flags"][k] else 0.0
            vals.append((float(r["pred"][k]) - y) ** 2)
        brier[HORIZONS[k]] = round(sum(vals) / max(1, len(vals)), 4)
    # constant baseline: train event rate at each horizon
    base = {}
    for k in range(len(HORIZONS)):
        rate = sum(1.0 for _, flags, _ in train if flags[k]) / max(1, len(train))
        base[HORIZONS[k]] = round(rate, 4)
    return {
        "n_train": len(train), "n_test": len(test),
        "brier": brier, "base_rate": base,
        "first_pred": rows[0] if rows else None,
    }
