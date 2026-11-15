from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import torch

from .qq_rank import feat, pairs
from .qq_rank_gate import fit

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def events(rows):
    by = defaultdict(list)
    for r in rows:
        by[(r["group"], r["ts"], tuple(r["context"]))].append(r)
    return list(by.values())


def aggregate(scores):
    scores = sorted(scores, reverse=True)
    top = scores[0]
    second = scores[1] if len(scores) > 1 else 0.0
    return {
        "max": top,
        "mean": sum(scores) / len(scores),
        "margin": top - second,
    }


def auc(y, s):
    pos = [v for v, t in zip(s, y) if t == 1]
    neg = [v for v, t in zip(s, y) if t == 0]
    if not pos or not neg: return 0.0
    good = 0.0
    for p in pos:
        for n in neg:
            good += 1.0 if p > n else 0.5 if p == n else 0.0
    return good / (len(pos) * len(neg))


def metrics(y, s, th):
    p = [1 if v >= th else 0 for v in s]
    rec = []
    for k in (0, 1):
        n = sum(1 for t in y if t == k)
        if n: rec.append(sum(1 for pi, ti in zip(p, y) if pi == k and ti == k) / n)
    return {"accuracy": round(sum(1 for pi, ti in zip(p, y) if pi == ti) / len(y), 4), "balanced": round(sum(rec) / max(1, len(rec)), 4), "speak_pred": sum(p)}


def run() -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    train = [r for r in rows if r["group"] not in (test_group, val_group)]
    val = [r for r in rows if r["group"] == val_group]
    test = [r for r in rows if r["group"] == test_group]
    model, mu, sd = fit(pairs(train))
    def score_event(ev):
        X = (torch.tensor([feat(r) for r in ev], dtype=torch.float32) - mu) / sd
        with torch.no_grad():
            return aggregate(model(X).detach().cpu().numpy().reshape(-1).tolist())
    def pack(evs):
        y = [1 if any(r["label"] == "speak" for r in ev) else 0 for ev in evs]
        aggs = [score_event(ev) for ev in evs]
        return y, aggs
    yv, av = pack(events(val)); yt, at = pack(events(test))
    best = (-1.0, 0.0)
    for key in ("max", "mean", "margin"):
        vals = [a[key] for a in av]
        for th in sorted(set(vals)):
            m = metrics(yv, vals, th)
            if m["balanced"] > best[0]: best = (m["balanced"], th, key)
    key = best[2]
    th = best[1]
    te_scores = [a[key] for a in at]
    return {
        "val_events": len(yv), "test_events": len(yt),
        "aggregate": key, "threshold": round(th, 4),
        "val_balanced": round(best[0], 4),
        "test_auc_max": round(auc(yt, [a["max"] for a in at]), 4),
        "test_auc_mean": round(auc(yt, [a["mean"] for a in at]), 4),
        "test_auc_margin": round(auc(yt, [a["margin"] for a in at]), 4),
        "test": metrics(yt, te_scores, th),
    }


if __name__ == "__main__":
    print(run())
