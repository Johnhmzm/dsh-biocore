from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import torch
import torch.nn as nn

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def feat(r):
    hour = float(r.get("hour", 0))
    return [
        float(r.get("target_at", 0)),
        math.log1p(float(r.get("target_recent", 0))),
        min(float(r.get("target_gap", 9999)), 3600) / 3600.0,
        math.log1p(float(r.get("group_recent", 0))),
        min(float(r.get("since_last", 9999)), 3600) / 3600.0,
        float(r.get("group_size", 0)) / 100.0,
        math.sin(2 * math.pi * hour),
        math.cos(2 * math.pi * hour),
        float(r.get("message_len", 0)),
        float(r.get("is_question", 0)),
        float(r.get("has_at", 0)),
        float(r.get("target_reciprocity", 0)),
    ]


def pairs(rows):
    by = defaultdict(list)
    for r in rows:
        by[(r["group"], r["ts"], tuple(r["context"]))].append(r)
    out = []
    for v in by.values():
        pos = [x for x in v if x["label"] == "speak"]
        neg = [x for x in v if x["label"] == "silence"]
        if pos and neg:
            out.append((pos[0], neg[0]))
    return out


def auc(pairs_, model, mu, sd):
    with torch.no_grad():
        good = 0.0
        for p, n in pairs_:
            sp = model((torch.tensor([feat(p)]) - mu) / sd).item()
            sn = model((torch.tensor([feat(n)]) - mu) / sd).item()
            good += 1.0 if sp > sn else 0.5 if sp == sn else 0.0
    return good / max(1, len(pairs_))


def train(epochs: int = 300) -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    train_rows = [r for r in rows if r["group"] not in (test_group, val_group)]
    val_rows = [r for r in rows if r["group"] == val_group]
    test_rows = [r for r in rows if r["group"] == test_group]
    tr, va, te = pairs(train_rows), pairs(val_rows), pairs(test_rows)
    X = torch.tensor([feat(p) for p, _ in tr] + [feat(n) for _, n in tr], dtype=torch.float32)
    mu, sd = X.mean(0), X.std(0) + 1e-6

    def enc(p): return (torch.tensor([feat(p)], dtype=torch.float32) - mu) / sd
    model = nn.Sequential(nn.Linear(len(feat(tr[0][0])), 1))
    opt = torch.optim.Adam(model.parameters(), lr=0.02)
    for _ in range(epochs):
        model.zero_grad()
        loss = torch.stack([torch.nn.functional.softplus(-(model(enc(p)) - model(enc(n))).squeeze()) for p, n in tr]).mean()
        loss.backward(); opt.step()
    return {
        "pairs_train": len(tr), "pairs_val": len(va), "pairs_test": len(te),
        "val_auc": round(auc(va, model, mu, sd), 4),
        "test_auc": round(auc(te, model, mu, sd), 4),
        "test_group": test_group, "val_group": val_group,
    }


if __name__ == "__main__":
    print(train())
