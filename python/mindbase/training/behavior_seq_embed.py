from __future__ import annotations

import json
import random
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, List, Tuple

from .behavior import load_annotations

CACHE = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR" / "embed_cache_qwen3_gguf.json"
LABELS = ("approach", "give_help", "explore", "avoid", "communicate")


def build(limit: int | None = None) -> Tuple[List[Tuple[str, List[List[float]], List[int]]], List[Tuple[str, List[List[float]], List[int]]]]:
    samples = [s for s in load_annotations(limit=limit) if s.action_kind in LABELS]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    groups: "OrderedDict[str, List]" = OrderedDict()
    for s in samples:
        groups.setdefault(s.group, []).append(s)
    seqs = []
    for g, rows in groups.items():
        xs = [cache[r.event] for r in rows if r.event in cache]
        ys = [LABELS.index(r.action_kind) for r in rows if r.event in cache]
        if xs:
            seqs.append((g, xs, ys))
    rng = random.Random(0); rng.shuffle(seqs)
    cut = int(len(seqs) * 0.75)
    return seqs[:cut], seqs[cut:]


def train(limit: int | None = None, epochs: int = 30) -> dict:
    import torch
    import torch.nn as nn

    train, test = build(limit)
    n_in = len(train[0][1][0])
    model = nn.GRU(n_in, 64, batch_first=True)
    head = nn.Linear(64, len(LABELS))
    opt = torch.optim.Adam(list(model.parameters()) + list(head.parameters()), lr=1e-3)

    def batch(rows):
        maxlen = max(len(x) for _, x, _ in rows)
        X = torch.zeros(len(rows), maxlen, n_in)
        Y = torch.full((len(rows), maxlen), -100, dtype=torch.long)
        for i, (_, xs, ys) in enumerate(rows):
            X[i, :len(xs)] = torch.tensor(xs)
            Y[i, :len(ys)] = torch.tensor(ys)
        return X, Y

    counts = Counter(y for _, _, ys in train for y in ys)
    w = torch.tensor([1.0 / max(1, counts.get(i, 0)) for i in range(len(LABELS))]); w = w / w.sum() * len(LABELS)
    loss_fn = nn.CrossEntropyLoss(ignore_index=-100, weight=w)
    X, Y = batch(train)
    for _ in range(epochs):
        model.zero_grad()
        loss = loss_fn(head(model(X)[0]).reshape(-1, len(LABELS)), Y.reshape(-1))
        loss.backward(); opt.step()

    def metrics(rows):
        X, Y = batch(rows)
        with torch.no_grad():
            p = head(model(X)[0]).argmax(-1)
        correct = total = 0; recalls = []
        for k in range(len(LABELS)):
            tp = sum(1 for i in range(Y.size(0)) for j in range(Y.size(1)) if Y[i, j] == k and p[i, j] == k)
            n = sum(1 for i in range(Y.size(0)) for j in range(Y.size(1)) if Y[i, j] == k)
            if n: recalls.append(tp / n)
            correct += tp; total += n
        return {"accuracy": round(correct / max(1, total), 4), "balanced": round(sum(recalls) / max(1, len(recalls)), 4), "n": total}

    return {"seqs": len(train) + len(test), "train": metrics(train), "test": metrics(test)}


if __name__ == "__main__":
    print(train())
