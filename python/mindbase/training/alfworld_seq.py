from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from typing import List, Tuple

from ..actions import ACTION_KINDS
from .alfworld import _extract_action, action_kind
from .features import event_features

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "ALFWorld" / "train_all_alfworld.jsonl"


def load_sequences(path: Path | str = DATA, limit_rows: int = 400) -> List[Tuple[str, List[List[float]], List[int]]]:
    out: List[Tuple[str, List[List[float]], List[int]]] = []
    with Path(path).open("r", encoding="utf-8", errors="ignore") as fh:
        for i, line in enumerate(fh):
            if i >= limit_rows:
                break
            try:
                row = json.loads(line)
            except Exception:
                continue
            xs: List[List[float]] = []
            ys: List[int] = []
            last_user = ""
            for msg in row.get("messages") or []:
                if msg.get("role") == "user":
                    last_user = str(msg.get("content") or "")
                elif msg.get("role") == "assistant":
                    action = _extract_action(str(msg.get("content") or ""))
                    if not action:
                        continue
                    kind = action_kind(action)
                    xs.append(event_features(last_user[-2000:]))
                    ys.append(ACTION_KINDS.index(kind))
            if xs:
                out.append((str(row.get("file_name") or i), xs, ys))
    return out


def train(limit_rows: int = 400, epochs: int = 15, seed: int = 0) -> dict:
    import torch
    import torch.nn as nn

    seqs = load_sequences(limit_rows=limit_rows)
    rng = random.Random(seed)
    rng.shuffle(seqs)
    cut = int(len(seqs) * 0.75)
    train, test = seqs[:cut], seqs[cut:]
    n_in = len(event_features("x"))
    model = nn.GRU(n_in, 64, batch_first=True)
    head = nn.Linear(64, len(ACTION_KINDS))
    params = list(model.parameters()) + list(head.parameters())
    opt = torch.optim.Adam(params, lr=1e-3)

    def batch(rows):
        maxlen = max(len(x) for _, x, _ in rows)
        X = torch.zeros(len(rows), maxlen, n_in)
        Y = torch.full((len(rows), maxlen), -100, dtype=torch.long)
        for i, (_, xs, ys) in enumerate(rows):
            X[i, :len(xs)] = torch.tensor(xs)
            Y[i, :len(ys)] = torch.tensor(ys)
        return X, Y

    counts = Counter(y for _, _, ys in train for y in ys)
    weights = torch.tensor([1.0 / max(1, counts.get(i, 0)) for i in range(len(ACTION_KINDS))])
    weights = weights / weights.sum() * len(ACTION_KINDS)
    loss_fn = nn.CrossEntropyLoss(ignore_index=-100, weight=weights)

    X, Y = batch(train)
    for _ in range(epochs):
        model.zero_grad()
        h, _ = model(X)
        loss = loss_fn(head(h).reshape(-1, len(ACTION_KINDS)), Y.reshape(-1))
        loss.backward()
        opt.step()

    def metrics(rows):
        X, Y = batch(rows)
        with torch.no_grad():
            p = head(model(X)[0]).argmax(-1)
        correct = total = 0
        recalls = []
        for k in range(len(ACTION_KINDS)):
            tp = sum(1 for i in range(Y.size(0)) for j in range(Y.size(1)) if Y[i, j] == k and p[i, j] == k)
            n = sum(1 for i in range(Y.size(0)) for j in range(Y.size(1)) if Y[i, j] == k)
            if n:
                recalls.append(tp / n)
            correct += tp
            total += n
        return {"accuracy": round(correct / max(1, total), 4), "balanced": round(sum(recalls) / max(1, len(recalls)), 4), "n": total}

    return {"seqs": len(seqs), "train": metrics(train), "test": metrics(test)}


if __name__ == "__main__":
    print(train())
