from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .linear import SoftmaxRegression

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "choices13k"


def _ev(lot) -> float:
    return sum(float(p) * float(v) for p, v in lot)


def _var(lot) -> float:
    mean = _ev(lot)
    return sum(float(p) * (float(v) - mean) ** 2 for p, v in lot)


def _p_win(lot) -> float:
    return sum(float(p) for p, v in lot if float(v) > 0)


def _v(x: float, alpha: float = 0.88, lam: float = 2.25) -> float:
    return x ** alpha if x >= 0 else -lam * ((-x) ** alpha)


def _w(p: float, gamma: float = 0.61) -> float:
    p = min(1.0, max(0.0, p))
    return p ** gamma / ((p ** gamma + (1.0 - p) ** gamma) ** (1.0 / gamma))


def _pt(lot) -> float:
    return sum(_w(float(p)) * _v(float(v)) for p, v in lot)


def features(lot_a, lot_b) -> List[float]:
    ev_a, ev_b = _ev(lot_a), _ev(lot_b)
    var_a, var_b = _var(lot_a), _var(lot_b)
    ml_a = min(float(v) for _, v in lot_a)
    ml_b = min(float(v) for _, v in lot_b)
    pw_a, pw_b = _p_win(lot_a), _p_win(lot_b)
    pt_a, pt_b = _pt(lot_a), _pt(lot_b)
    return [ev_a / 100, ev_b / 100, ev_a - ev_b, var_a / 1000, var_b / 1000, var_a - var_b,
            ml_a / 100, ml_b / 100, ml_a - ml_b, pw_a, pw_b, pw_a - pw_b,
            pt_a / 100, pt_b / 100, pt_a - pt_b]


@dataclass
class ChoiceSample:
    problem: str
    lot_a: list
    lot_b: list
    choice: str
    features: List[float]


def load_choice_samples(limit: int = 3000) -> List[ChoiceSample]:
    problems = json.loads((DATA / "c13k_problems.json").read_text(encoding="utf-8"))
    out: List[ChoiceSample] = []
    with (DATA / "c13k_selections.csv").open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            pid = str(row.get("Problem") or "")
            if pid not in problems:
                continue
            lot_a = problems[pid].get("A", [])
            lot_b = problems[pid].get("B", [])
            if not lot_a or not lot_b:
                continue
            try:
                b_rate = float(row.get("bRate") or 0.5)
            except ValueError:
                continue
            out.append(ChoiceSample(pid, lot_a, lot_b, "B" if b_rate >= 0.5 else "A", features(lot_a, lot_b)))
            if len(out) >= limit:
                break
    return out


class ChoiceModel:
    def __init__(self, n_features: int, lr: float = 0.1):
        self.clf = SoftmaxRegression(n_features, 2, lr=lr)

    def fit(self, samples: Sequence[ChoiceSample], epochs: int = 120) -> "ChoiceModel":
        X = [s.features for s in samples]
        y = [0 if s.choice == "A" else 1 for s in samples]
        self.clf.fit(X, y, epochs=epochs, seed=0)
        return self

    def accuracy(self, samples: Sequence[ChoiceSample]) -> float:
        X = [s.features for s in samples]
        y = [0 if s.choice == "A" else 1 for s in samples]
        return round(self.clf.accuracy(X, y), 4)
