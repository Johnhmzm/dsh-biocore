from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Sequence

from ..actions import ACTION_KINDS
from .dataset import StorySample
from .social_eval import load_tombench
from .features import sample_vector
from .linear import SoftmaxRegression

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "general_actions.json"
TOMBENCH = Path(__file__).resolve().parent.parent / "data" / "real" / "ToMBench"
CHOICES13K = Path(__file__).resolve().parent.parent / "data" / "real" / "choices13k"


def load_general_actions(path: Path | None = None) -> List[StorySample]:
    raw = json.loads(Path(path or DATA_PATH).read_text(encoding="utf-8"))
    return [StorySample.from_dict(x) for x in raw]


def load_tombench_actions(path: Path | None = None) -> List[StorySample]:
    """Real social-action items, mapped to the base's generic action kinds."""
    samples = load_tombench(path or TOMBENCH)
    out: List[StorySample] = []
    for i, s in enumerate(samples):
        if s.source not in ("Prediction of Actions", "Completion of Failed Actions"):
            continue
        kind = s.choices.get(s.answer, "")
        out.append(
            StorySample(
                event=s.context or s.question,
                state={"stress": 0.4, "social": 0.5, "mood": 0.0, "energy": 0.6, "identity": 0.7},
                appraisal={"valence": 0.0, "arousal": 0.5, "threat": 0.2, "social": 0.5, "relevance": 0.7},
                action=kind,
                action_kind=kind,
                domain="social",
                modality="none",
                outcome={"stress": 0.35, "social": 0.55, "mood": 0.0, "energy": 0.6, "identity": 0.7},
                reward=0.0,
                group=f"tom_action:{i}",
            )
        )
    return out


def _ev(lot) -> float:
    return sum(float(p) * float(v) for p, v in lot)


def _var(lot) -> float:
    mean = _ev(lot)
    return sum(float(p) * (float(v) - mean) ** 2 for p, v in lot)


def _p_win(lot) -> float:
    return sum(float(p) for p, v in lot if float(v) > 0)


def _max_loss(lot) -> float:
    return min(float(v) for _, v in lot)


def _lot_text(name: str, lot) -> str:
    return name + ": " + ", ".join(f"{float(p):.3f}*{float(v):.1f}" for p, v in lot)


def load_choices13k(limit: int = 2000) -> List[StorySample]:
    problems = json.loads((CHOICES13K / "c13k_problems.json").read_text(encoding="utf-8"))
    rows: List[StorySample] = []
    with (CHOICES13K / "c13k_selections.csv").open("r", encoding="utf-8-sig", newline="") as fh:
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
            chosen = lot_b if b_rate >= 0.5 else lot_a
            other = lot_a if b_rate >= 0.5 else lot_b
            vc, vo = _var(chosen), _var(other)
            if abs(vc - vo) < 1e-9:
                continue
            kind = "approach" if vc > vo else "avoid"
            ev_a, ev_b = _ev(lot_a), _ev(lot_b)
            var_a, var_b = _var(lot_a), _var(lot_b)
            ml_a, ml_b = _max_loss(lot_a), _max_loss(lot_b)
            pw_a, pw_b = _p_win(lot_a), _p_win(lot_b)
            rows.append(
                StorySample(
                    event=f"{_lot_text('A', lot_a)} | {_lot_text('B', lot_b)}",
                    state={"stress": 0.3, "social": 0.4, "mood": 0.0, "energy": 0.6, "identity": 0.7},
                    appraisal={"valence": 0.0, "arousal": 0.5, "threat": 0.2, "social": 0.3, "relevance": 0.7},
                    action=kind,
                    action_kind=kind,
                    domain="decision",
                    modality="none",
                    outcome={"stress": 0.3, "social": 0.4, "mood": 0.0, "energy": 0.6, "identity": 0.7},
                    reward=0.0,
                    group=f"choices13k:{pid}",
                    structured={
                        "ev_a": ev_a, "ev_b": ev_b,
                        "var_a": var_a, "var_b": var_b,
                        "max_loss_a": ml_a, "max_loss_b": ml_b,
                        "p_win_a": pw_a, "p_win_b": pw_b,
                        "ev_gap": ev_a - ev_b, "var_gap": var_a - var_b,
                        "max_loss_gap": ml_a - ml_b, "p_win_gap": pw_a - pw_b,
                    },
                )
            )
            if len(rows) >= limit:
                break
    return rows


class GeneralPolicyModel:
    """Action-kind policy, not chat style: communicate/approach/avoid/..."""

    def __init__(self, n_features: int, lr: float = 0.15):
        self.clf = SoftmaxRegression(n_features, len(ACTION_KINDS), lr=lr)

    def fit(self, samples: Sequence[StorySample], epochs: int = 200, balanced: bool = False) -> "GeneralPolicyModel":
        rows = [s for s in samples if s.action_kind in ACTION_KINDS]
        if balanced and rows:
            import random
            by_label = {}
            for s in rows:
                by_label.setdefault(s.action_kind, []).append(s)
            cap = max(len(v) for v in by_label.values())
            rng = random.Random(0)
            rows = [rng.choice(v) for v in by_label.values() for _ in range(cap)]
        X = [sample_vector(s.event, s.state, getattr(s, 'structured', None)) for s in rows]
        y = [ACTION_KINDS.index(s.action_kind) for s in rows]
        self.clf.fit(X, y, epochs=epochs)
        return self

    def predict(self, event: str, state: Dict[str, float], structured=None) -> str:
        return ACTION_KINDS[self.clf.predict(sample_vector(event, state, structured))]

    def predict_proba(self, event: str, state: Dict[str, float], structured=None) -> Dict[str, float]:
        p = self.clf.predict_proba(sample_vector(event, state, structured))
        return {k: round(v, 4) for k, v in zip(ACTION_KINDS, p)}

    def accuracy(self, samples: Sequence[StorySample]) -> float:
        rows = [s for s in samples if s.action_kind in ACTION_KINDS]
        X = [sample_vector(s.event, s.state, getattr(s, 'structured', None)) for s in rows]
        y = [ACTION_KINDS.index(s.action_kind) for s in rows]
        return round(self.clf.accuracy(X, y), 3)
