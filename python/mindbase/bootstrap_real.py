"""Train local appraisal + timing heads from REAL datasets.

Data (already local):
  EmoBank 10k  -> valence/arousal
  CPED 11k     -> Chinese emotion/DA/Big Five
  ESConv 1.3k  -> support strategies
  When2Speak 21.8k -> SPEAK vs SILENT timing

No LLM is called. Output: mindbase/data/bootstrap_heads_real.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .training.appraisal import TARGETS, AppraisalModel, CompositeAppraisalModel
from .training.dataset import StorySample
from .training.features import feature_names
from .training.loaders import load_cped, load_emobank, load_esconv
from .training.persist import save_heads
from .training.timing import (TimingModel, load_human_thread_pairs, load_spontaneous_pairs,
                              load_when2speak_pairs)

DATA = Path(__file__).resolve().parent / "data"
REAL = DATA / "real"
DEFAULT_OUT = DATA / "bootstrap_heads_real.json"
DEFAULT_SUMMARY = DATA / "bootstrap_real_summary.json"


def _limit(n: int):
    return None if int(n) <= 0 else int(n)


def load_real_data(max_per_source: int = 4000) -> Dict[str, List[StorySample]]:
    lim = _limit(max_per_source)
    return {
        "emobank": load_emobank(REAL / "EmoBank" / "emobank.csv", limit=lim),
        "cped": load_cped(REAL / "CPED" / "valid_split.csv", limit=lim),
        "esconv": load_esconv(REAL / "ESConv" / "ESConv.json", limit=lim),
    }


def _split_by_group(samples: Sequence[StorySample], test_ratio: float = 0.2, seed: int = 0) -> Tuple[List[StorySample], List[StorySample]]:
    groups: Dict[str, List[StorySample]] = {}
    for s in samples:
        key = s.domain + ":" + (s.group or "default")
        groups.setdefault(key, []).append(s)
    keys = list(groups)
    random.Random(int(seed)).shuffle(keys)
    n_test = max(1, int(round(len(keys) * float(test_ratio)))) if keys else 0
    test_keys = set(keys[:n_test])
    train: List[StorySample] = []
    test: List[StorySample] = []
    for key, rows in groups.items():
        (test if key in test_keys else train).extend(rows)
    return train, test


def _split_appraisal(samples: Sequence[StorySample], seed: int = 0):
    """Honor EmoBank train/test; group-split CPED/ESConv conversations."""
    train: List[StorySample] = []
    test: List[StorySample] = []
    by_group: Dict[str, List[StorySample]] = {}
    for s in samples:
        if s.domain == "appraisal":
            (test if str(s.group) == "test" else train).append(s)
        else:
            by_group.setdefault(s.domain + ":" + (s.group or "default"), []).append(s)
    keys = list(by_group)
    random.Random(int(seed)).shuffle(keys)
    n_test = max(1, int(round(len(keys) * 0.2))) if keys else 0
    test_keys = set(keys[:n_test])
    for key, rows in by_group.items():
        (test if key in test_keys else train).extend(rows)
    return train, test

def _appraisal_mae(model: AppraisalModel, samples: Sequence[StorySample]) -> Dict[str, float]:
    if not samples:
        return {}
    targets = tuple(getattr(model, "targets", TARGETS))
    err = {k: 0.0 for k in targets}
    for s in samples:
        pred = model.predict(s.event, s.state)
        for k in targets:
            err[k] += abs(float(pred.get(k, 0.0)) - float(s.appraisal.get(k, 0.0)))
    return {"mae_" + k: round(v / len(samples), 4) for k, v in err.items()}


def _n_speakers(context: str) -> int:
    return len(set(re.findall(r"Speaker_(\d+):", str(context or ""))))

def _split_pairs(pairs: Sequence[Tuple[str, int]], test_ratio: float = 0.2, seed: int = 0):
    idx = list(range(len(pairs)))
    random.Random(int(seed)).shuffle(idx)
    n_test = max(1, int(round(len(idx) * float(test_ratio)))) if idx else 0
    test = [pairs[i] for i in idx[:n_test]]
    train = [pairs[i] for i in idx[n_test:]]
    return train, test


def _balance_pairs(pairs: Sequence[Tuple[str, int]], seed: int = 0, cap: int = 3000):
    """Oversample the rare SPEAK class and keep all SILENT examples."""
    speak = [p for p in pairs if int(p[1]) == 1]
    silent = [p for p in pairs if int(p[1]) == 0]
    if not speak or not silent:
        return list(pairs)
    mult = max(1, min(4, len(silent) // max(1, len(speak))))
    out = list(silent) + speak * mult
    random.Random(int(seed)).shuffle(out)
    return out


def _timing_metrics(model: TimingModel, pairs) -> Dict[str, object]:
    if not pairs:
        return {"n": 0}
    y = []
    pred = []
    for item in pairs:
        ctx, label = item[0], int(item[1])
        state = item[2] if len(item) > 2 else None
        y.append(label)
        pred.append(1 if model.predict(ctx, state) == "speak" else 0)
    acc = sum(1 for a, b in zip(y, pred) if a == b) / len(y)
    rec, prec, f1 = [], [], []
    for lab in (0, 1):
        tp = sum(1 for a, b in zip(y, pred) if a == lab and b == lab)
        fn = sum(1 for a, b in zip(y, pred) if a == lab and b != lab)
        fp = sum(1 for a, b in zip(y, pred) if a != lab and b == lab)
        r = tp / (tp + fn) if (tp + fn) else 0.0
        p = tp / (tp + fp) if (tp + fp) else 0.0
        rec.append(r)
        prec.append(p)
        f1.append(2 * p * r / (p + r) if (p + r) else 0.0)
    return {
        "n": len(y),
        "accuracy": round(acc, 4),
        "balanced_accuracy": round(sum(rec) / 2.0, 4),
        "macro_f1": round(sum(f1) / 2.0, 4),
        "speak_recall": round(rec[1], 4),
        "speak_precision": round(prec[1], 4),
        "true": dict(Counter("speak" if x == 1 else "silence" for x in y)),
        "pred": dict(Counter("speak" if x == 1 else "silence" for x in pred)),
    }


def train_real_heads(max_per_source: int = 4000, epochs: int = 60,
                     seed: int = 1, out: Path | str = DEFAULT_OUT) -> Dict[str, object]:
    t0 = time.perf_counter()
    data = load_real_data(max_per_source=max_per_source)
    va_rows = list(data["emobank"])
    # CPED is the chat-domain source.  ESConv is kept as a support/action
    # source, not mixed into the chat appraisal head (labels/domain differ).
    chat_rows = list(data["cped"])
    es_rows = list(data["esconv"])
    appraisal_rows = va_rows + chat_rows + es_rows
    va_train, va_test = _split_appraisal(va_rows, seed=seed)
    chat_train, chat_test = _split_appraisal(chat_rows, seed=seed)
    _es_train, es_test = _split_appraisal(es_rows, seed=seed)
    ap_test = va_test + chat_test

    when2_all = load_when2speak_pairs(REAL / "When2Speak" / "finetune_val_token.jsonl")
    when2_1v1 = [p for p in when2_all if _n_speakers(p[0]) <= 2]
    human_all = load_human_thread_pairs(DATA / "human_thread.jsonl")
    spont_all = load_spontaneous_pairs(DATA / "spontaneous_48.jsonl")
    w2_train, w2_test = _split_pairs(when2_1v1, test_ratio=0.2, seed=seed)
    h_train, h_test = _split_pairs(human_all, test_ratio=0.25, seed=seed)
    s_train, s_test = _split_pairs(spont_all, test_ratio=0.25, seed=seed)
    timing_train = w2_train + h_train * 5 + s_train * 5
    timing_bal = _balance_pairs(timing_train, seed=seed, cap=3000)

    n_features = len(feature_names())
    va_head = AppraisalModel(n_features).fit(va_train, epochs=int(epochs))
    vad_head = AppraisalModel(n_features, targets=("valence", "arousal", "dominance")).fit(va_train, epochs=int(epochs))
    chat_head = AppraisalModel(n_features).fit(chat_train, epochs=int(epochs))
    # The base runtime is chat; use the chat-domain head for all five targets.
    # EmoBank head is kept for calibration/reporting, not runtime.
    appraisal = chat_head
    timing = TimingModel().fit(timing_bal, epochs=100)
    timing.calibrate(h_train + s_train, metric="f1")

    metrics: Dict[str, object] = {
        "sources": {k: len(v) for k, v in data.items()},
        "when2speak_pairs": len(when2_all),
        "when2speak_1v1_pairs": len(when2_1v1),
        "human_thread_pairs": len(human_all),
        "spontaneous_pairs": len(spont_all),
        "appraisal_rows": len(appraisal_rows),
        "appraisal_train": len(va_train) + len(chat_train),
        "appraisal_test": len(ap_test),
        "appraisal_cped": _appraisal_mae(chat_head, chat_test),
        "appraisal_esconv_generalization": _appraisal_mae(chat_head, es_test),
        "appraisal_emobank": _appraisal_mae(va_head, va_test),
        "appraisal_vad_emobank": _appraisal_mae(vad_head, va_test),
        "timing_train": len(timing_bal),
        "timing_threshold": round(float(timing.threshold), 3),
        "timing_when2_1v1": _timing_metrics(timing, w2_test),
        "timing_human": _timing_metrics(timing, h_test),
        "timing_spontaneous": _timing_metrics(timing, s_test),
        "timing_train_mix": dict(Counter("speak" if int(v) == 1 else "silence" for v in [p[1] for p in timing_bal])),
        "elapsed_sec": round(time.perf_counter() - t0, 3),
    }
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_heads({"appraisal": appraisal, "vad": vad_head}, path)
    metrics["heads_path"] = str(path)
    summary = Path(DEFAULT_SUMMARY)
    summary.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics["summary_path"] = str(summary)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Train real appraisal + timing heads (no LLM)")
    parser.add_argument("--max-per-source", type=int, default=4000)
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    metrics = train_real_heads(max_per_source=args.max_per_source, epochs=args.epochs,
                               seed=args.seed, out=args.out)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
