from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .appraisal import TARGETS, AppraisalModel
from .balance import balance_actions, action_counts
from .general import GeneralPolicyModel, load_general_actions
from ..actions import ACTION_KINDS
from .dataset import ACTION_LABELS, StorySample, load_samples
from .features import feature_names, sample_vector
from .loaders import load_all, load_cped, load_emobank
from .policy import PolicyModel
from .world_model import NEXT_KEYS, WorldModel

REAL = Path(__file__).resolve().parent.parent / "data" / "real"


def group_split(samples: Sequence[StorySample], test_ratio: float = 0.25, seed: int = 0) -> Tuple[List[StorySample], List[StorySample]]:
    groups: Dict[str, List[StorySample]] = defaultdict(list)
    for i, sample in enumerate(samples):
        groups[sample.group or f"sample:{i}"].append(sample)
    if "train" in groups and "test" in groups:
        return groups["train"], groups["test"]
    keys = list(groups)
    random.Random(seed).shuffle(keys)
    n_test = max(1, int(round(len(keys) * test_ratio)))
    test_keys = set(keys[:n_test])
    train: List[StorySample] = []
    test: List[StorySample] = []
    for key, rows in groups.items():
        (test if key in test_keys else train).extend(rows)
    return train, test


def macro_f1(y_true: Sequence[int], y_pred: Sequence[int], n_labels: int) -> float:
    scores = []
    for label in range(n_labels):
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        if precision + recall > 0:
            scores.append(2 * precision * recall / (precision + recall))
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def policy_metrics(model: PolicyModel, samples: Sequence[StorySample]) -> Dict[str, float]:
    rows = [s for s in samples if s.action in ACTION_LABELS]
    if not rows:
        return {"accuracy": 0.0, "macro_f1": 0.0, "majority": 0.0, "n": 0}
    y_true = [ACTION_LABELS.index(s.action) for s in rows]
    y_pred = [model.clf.predict(sample_vector(s.event, s.state, getattr(s, 'structured', None))) for s in rows]
    majority = Counter(y_true).most_common(1)[0][1] / len(y_true)
    recalls = []
    for label in set(y_true):
        total = sum(1 for t in y_true if t == label)
        hit = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        recalls.append(hit / total if total else 0.0)
    return {
        "accuracy": round(sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(y_true), 4),
        "balanced_accuracy": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "macro_f1": macro_f1(y_true, y_pred, len(ACTION_LABELS)),
        "majority": round(majority, 4),
        "n": len(y_true),
    }


def general_policy_metrics(model: GeneralPolicyModel, samples: Sequence[StorySample]) -> Dict[str, float]:
    rows = [s for s in samples if s.action_kind in ACTION_KINDS]
    if not rows:
        return {"accuracy": 0.0, "macro_f1": 0.0, "n": 0}
    y_true = [ACTION_KINDS.index(s.action_kind) for s in rows]
    y_pred = [model.clf.predict(sample_vector(s.event, s.state, getattr(s, 'structured', None))) for s in rows]
    recalls = []
    for label in set(y_true):
        total = sum(1 for t in y_true if t == label)
        hit = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        recalls.append(hit / total if total else 0.0)
    return {
        "accuracy": round(sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(rows), 4),
        "balanced_accuracy": round(sum(recalls) / len(recalls), 4),
        "macro_f1": macro_f1(y_true, y_pred, len(ACTION_KINDS)),
        "n": len(rows),
    }


def regression_metrics(model, samples: Sequence[StorySample], targets: Sequence[str], kind: str = "appraisal") -> Dict[str, float]:
    if not samples:
        return {}
    errors = {k: 0.0 for k in targets}
    for s in samples:
        if kind == "appraisal":
            pred = model.predict(s.event, s.state)
            true = s.appraisal
        else:
            pred = model.predict(s.event, s.state, s.action)
            true = dict(s.outcome)
            true["reward"] = s.reward
        for k in targets:
            errors[k] += abs(float(pred.get(k, 0.0)) - float(true.get(k, 0.0)))
    return {f"mae_{k}": round(v / len(samples), 4) for k, v in errors.items()}


def evaluate_split(
    train: Sequence[StorySample],
    test: Sequence[StorySample],
    epochs: int = 100,
    balance_train: bool = True,
) -> Dict[str, object]:
    raw_counts = action_counts(list(train))
    if balance_train:
        train = balance_actions(list(train), max_ratio=2.0, seed=0)
    n_features = len(feature_names())
    appraisal = AppraisalModel(n_features).fit(train, epochs=epochs)
    policy = PolicyModel(n_features).fit(train, epochs=epochs)
    general = GeneralPolicyModel(n_features).fit(train, epochs=epochs)
    world = WorldModel(n_features).fit(train, epochs=epochs)
    return {
        "n_train": len(train),
        "n_test": len(test),
        "n_groups": len({s.group for s in train + list(test)}),
        "domains": dict(Counter(s.domain for s in list(train) + list(test))),
        "policy": policy_metrics(policy, test),
        "general_policy": general_policy_metrics(general, test),
        "action_counts_raw": raw_counts,
        "action_counts_train": action_counts(list(train)),
        "appraisal": regression_metrics(appraisal, test, TARGETS, "appraisal"),
        "world": regression_metrics(world, test, NEXT_KEYS, "world"),
    }


def main() -> None:
    cped_path = REAL / "CPED" / "valid_split.csv"
    emo_path = REAL / "EmoBank" / "emobank.csv"
    if cped_path.exists():
        cped = load_cped(cped_path, limit=1200)
        train, test = group_split(cped, seed=1)
        print("CPED real:", json.dumps(evaluate_split(train, test, epochs=80), indent=2, ensure_ascii=False))
    if emo_path.exists():
        emo = load_emobank(emo_path, limit=800)
        train, test = group_split(emo, seed=2)
        print("EmoBank real:", json.dumps(evaluate_split(train, test, epochs=80), indent=2, ensure_ascii=False))
    if not cped_path.exists() and not emo_path.exists():
        samples = load_samples()
        train, test = group_split(samples, seed=3)
        print("fixture:", json.dumps(evaluate_split(train, test, epochs=80), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
