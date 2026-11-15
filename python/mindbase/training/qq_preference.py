from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import torch
import torch.nn as nn

from ..embedding import EmbeddingClient

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def feat(r):
    return [
        float(r.get("target_at", 0)),
        math.log1p(float(r.get("target_recent", 0))) / 3.0,
        min(float(r.get("target_gap", 9999)), 3600) / 3600.0,
        math.log1p(float(r.get("group_recent", 0))) / 3.0,
        min(float(r.get("since_last", 9999)), 3600) / 3600.0,
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


def train(epochs: int = 100) -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    train = [r for r in rows if r["group"] not in (test_group, val_group)]
    val = [r for r in rows if r["group"] == val_group]
    test = [r for r in rows if r["group"] == test_group]
    client = EmbeddingClient.from_local_gguf(); client.timeout = 120
    cache = {}

    def vec(r):
        text = "\n".join(r["context"])
        if text not in cache:
            cache[text] = client.embed([text])[0]
        return cache[text] + feat(r)

    def score(model, r):
        return model(torch.tensor([vec(r)], dtype=torch.float32)).squeeze()

    tr = pairs(train); va = pairs(val); te = pairs(test)
    dim = len(vec(tr[0][0]))
    model = nn.Sequential(nn.Linear(dim, 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, 1))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    for _ in range(epochs):
        model.zero_grad()
        losses = []
        for pos, neg in tr:
            losses.append(torch.nn.functional.softplus(-(score(model, pos) - score(model, neg))))
        torch.stack(losses).mean().backward(); opt.step()

    def acc(ps):
        if not ps: return 0.0
        with torch.no_grad():
            return sum(1 for p, n in ps if score(model, p) > score(model, n)) / len(ps)
    return {
        "pairs_train": len(tr), "pairs_val": len(va), "pairs_test": len(te),
        "val_pairwise": round(acc(va), 4), "test_pairwise": round(acc(te), 4),
        "test_group": test_group, "val_group": val_group,
    }


if __name__ == "__main__":
    print(train())
