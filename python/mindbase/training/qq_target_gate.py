from __future__ import annotations

import json
import math
from pathlib import Path

import torch
import torch.nn as nn

from ..embedding import EmbeddingClient

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def feats(r):
    return [
        float(r.get("target_at", 0)),
        math.log1p(float(r.get("target_recent", 0))) / 3.0,
        min(float(r.get("target_gap", 9999)), 3600) / 3600.0,
        math.log1p(float(r.get("group_recent", 0))) / 3.0,
        min(float(r.get("since_last", 9999)), 3600) / 3600.0,
    ]


def train(epochs: int = 80) -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    train = [r for r in rows if r["group"] not in (test_group, val_group)]
    val = [r for r in rows if r["group"] == val_group]
    test = [r for r in rows if r["group"] == test_group]
    client = EmbeddingClient.from_local_gguf()
    client.timeout = 120

    def X(rs):
        emb = client.embed(["\n".join(r["context"]) for r in rs])
        return torch.tensor([e + feats(r) for e, r in zip(emb, rs)])

    def Y(rs):
        return torch.tensor([1 if r["label"] == "speak" else 0 for r in rs])

    Xtr, ytr = X(train), Y(train)
    Xv, yv = X(val), Y(val)
    Xt, yt = X(test), Y(test)
    model = nn.Sequential(nn.Linear(Xtr.shape[1], 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, 2))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    w = torch.tensor([1.0 / max(1, int((ytr == k).sum())) for k in (0, 1)]); w = w / w.sum() * 2
    loss_fn = nn.CrossEntropyLoss(weight=w)
    for _ in range(epochs):
        model.zero_grad(); loss = loss_fn(model(Xtr), ytr); loss.backward(); opt.step()

    def bal(pred, y):
        rec = []
        for k in (0, 1):
            n = int((y == k).sum())
            if n: rec.append(int(((pred == k) & (y == k)).sum()) / n)
        return sum(rec) / max(1, len(rec))

    with torch.no_grad():
        pv = torch.softmax(model(Xv), -1)[:, 1]
        pt = torch.softmax(model(Xt), -1)[:, 1]
    best = (-1.0, 0.5)
    for th in [x / 100 for x in range(10, 91, 5)]:
        b = bal((pv >= th).long(), yv)
        if b > best[0]: best = (b, th)
    th = best[1]
    p = (pt >= th).long()
    return {
        "train": len(train), "val": len(val), "test": len(test),
        "val_group": val_group, "test_group": test_group,
        "val_balanced": round(best[0], 4), "threshold": th,
        "test_accuracy": round(float((p == yt).float().mean()), 4),
        "test_balanced": round(bal(p, yt), 4),
        "test_speak": int((yt == 1).sum()), "test_silence": int((yt == 0).sum()),
        "pred_speak": int((p == 1).sum()),
    }


if __name__ == "__main__":
    print(train())
