from __future__ import annotations

import json
from pathlib import Path

import torch
import torch.nn as nn

from ..embedding import EmbeddingClient

DATA = Path(__file__).resolve().parent.parent / "data" / "gate_dataset_human.jsonl"
DEFAULT = Path(__file__).resolve().parent.parent / "data" / "qq_gate.pt"


def load():
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows


def train(epochs: int = 80) -> dict:
    rows = load()
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group = groups[0]
    val_group = groups[1]
    train = [r for r in rows if r["group"] not in (test_group, val_group)]
    val = [r for r in rows if r["group"] == val_group]
    test = [r for r in rows if r["group"] == test_group]
    client = EmbeddingClient.from_local_gguf()
    client.timeout = 120
    X = torch.tensor(client.embed(["\n".join(r["context"]) for r in train]))
    y = torch.tensor([1 if r["label"] == "speak" else 0 for r in train])
    Xv = torch.tensor(client.embed(["\n".join(r["context"]) for r in val]))
    yv = torch.tensor([1 if r["label"] == "speak" else 0 for r in val])
    Xt = torch.tensor(client.embed(["\n".join(r["context"]) for r in test]))
    yt = torch.tensor([1 if r["label"] == "speak" else 0 for r in test])
    model = nn.Sequential(nn.Linear(X.shape[1], 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, 2))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    w = torch.tensor([1.0 / max(1, int((y == k).sum())) for k in (0, 1)]); w = w / w.sum() * 2
    loss_fn = nn.CrossEntropyLoss(weight=w)
    for _ in range(epochs):
        model.zero_grad(); loss = loss_fn(model(X), y); loss.backward(); opt.step()
    with torch.no_grad():
        pv = torch.softmax(model(Xv), -1)[:, 1]
        pt = torch.softmax(model(Xt), -1)[:, 1]
    best = (-1.0, 0.5)
    for th in [x / 100 for x in range(10, 91, 5)]:
        pred = (pv >= th).long()
        rec = []
        for k in (0, 1):
            n = int((yv == k).sum())
            if n: rec.append(int(((pred == k) & (yv == k)).sum()) / n)
        bal = sum(rec) / max(1, len(rec))
        if bal > best[0]: best = (bal, th)
    th = best[1]
    p = (pt >= th).long()
    recalls = []
    for k in (0, 1):
        n = int((yt == k).sum())
        if n: recalls.append(int(((p == k) & (yt == k)).sum()) / n)
    return {
        "train": len(train),
        "val": len(val),
        "val_group": val_group,
        "val_balanced": round(best[0], 4),
        "threshold": th,
        "test": len(test),
        "test_group": test_group,
        "accuracy": round(float((p == yt).float().mean()), 4),
        "balanced": round(sum(recalls) / max(1, len(recalls)), 4),
        "test_speak": int((yt == 1).sum()),
        "test_silence": int((yt == 0).sum()),
        "pred_speak": int((p == 1).sum()),
    }


if __name__ == "__main__":
    print(train())
