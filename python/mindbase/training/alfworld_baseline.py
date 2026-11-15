from __future__ import annotations

import json
from pathlib import Path

from .alfworld import load_alfworld_sft
from .evaluate import general_policy_metrics, group_split
from .features import feature_names
from .general import GeneralPolicyModel

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "ALFWorld" / "train_all_alfworld.jsonl"
OUT = DATA.parent / "policy_baseline.json"


def run(limit: int = 1200, epochs: int = 60, save: bool = True, history: bool = False) -> dict:
    samples = load_alfworld_sft(DATA, limit=limit, history=history)
    train, test = group_split(samples, test_ratio=0.25, seed=9)
    result = {"n": len(samples), "n_train": len(train), "n_test": len(test), "history": history}
    for balanced in (False, True):
        model = GeneralPolicyModel(len(feature_names())).fit(train, epochs=epochs, balanced=balanced)
        result["balanced" if balanced else "unbalanced"] = {
            "train": general_policy_metrics(model, train),
            "test": general_policy_metrics(model, test),
        }
    if save:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
