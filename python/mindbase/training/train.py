from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .appraisal import AppraisalModel
from .dataset import StorySample, load_samples, train_test_split
from .features import feature_names
from .general import GeneralPolicyModel
from .policy import PolicyModel
from .world_model import WorldModel


def train_all(samples: Optional[List[StorySample]] = None, epochs: int = 250, seed: int = 0) -> Dict[str, object]:
    data = list(samples or load_samples())
    train, test = train_test_split(data, test_ratio=0.2, seed=seed)
    n_features = len(feature_names())

    appraisal = AppraisalModel(n_features).fit(train, epochs=epochs)
    policy = PolicyModel(n_features).fit(train, epochs=epochs)
    general = GeneralPolicyModel(n_features).fit(train, epochs=epochs)
    world = WorldModel(n_features).fit(train, epochs=epochs)

    metrics = {
        "n_train": len(train),
        "n_test": len(test),
        "policy_train_acc": policy.accuracy(train),
        "policy_test_acc": policy.accuracy(test),
        "general_train_acc": general.accuracy(train),
        "general_test_acc": general.accuracy(test),
        "appraisal_train_loss": appraisal.loss(train),
        "appraisal_test_loss": appraisal.loss(test),
        "world_train_loss": world.loss(train),
        "world_test_loss": world.loss(test),
    }
    return {"appraisal": appraisal, "policy": policy, "general_policy": general, "world": world, "metrics": metrics, "train": train, "test": test}


def main() -> None:
    out = train_all()
    print(json.dumps(out["metrics"], indent=2, ensure_ascii=False))
    policy: PolicyModel = out["policy"]
    print("sample predictions:")
    for s in out["test"][:3]:
        print(" ", s.event, "->", policy.predict(s.event, s.state), "| true", s.action)


if __name__ == "__main__":
    main()
