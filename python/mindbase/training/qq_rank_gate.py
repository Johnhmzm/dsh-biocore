from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import torch
import torch.nn as nn

from .qq_rank import feat, pairs

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def fit(tr, epochs=250):
    Xp = torch.tensor([feat(p) for p, _ in tr], dtype=torch.float32)
    Xn = torch.tensor([feat(n) for _, n in tr], dtype=torch.float32)
    X = torch.cat([Xp, Xn], 0)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xp = (Xp - mu) / sd; Xn = (Xn - mu) / sd
    model = nn.Sequential(nn.Linear(X.shape[1], 16), nn.ReLU(), nn.Linear(16, 1))
    opt = torch.optim.Adam(model.parameters(), lr=0.02)
    for _ in range(epochs):
        model.zero_grad()
        loss = torch.nn.functional.softplus(-(model(Xp).squeeze() - model(Xn).squeeze())).mean()
        loss.backward(); opt.step()
    return model, mu, sd


def scores(rows, model, mu, sd):
    X = (torch.tensor([feat(r) for r in rows], dtype=torch.float32) - mu) / sd
    with torch.no_grad():
        return model(X).squeeze().numpy()


def metrics(y, s, th):
    p = (s >= th).astype(int)
    rec = []
    for k in (0, 1):
        n = int((y == k).sum())
        if n: rec.append(int(((p == k) & (y == k)).sum()) / n)
    return {
        "accuracy": round(float((p == y).mean()), 4),
        "balanced": round(sum(rec) / max(1, len(rec)), 4),
        "silence_recall": round(rec[0], 4) if rec else 0.0,
        "pred_speak": int((p == 1).sum()),
    }


def train():
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    tr_rows = [r for r in rows if r["group"] not in (test_group, val_group)]
    va_rows = [r for r in rows if r["group"] == val_group]
    te_rows = [r for r in rows if r["group"] == test_group]
    tr = pairs(tr_rows)
    model, mu, sd = fit(tr)
    va = scores(va_rows, model, mu, sd)
    va = (va - va.mean()) / (va.std() + 1e-6)
    yv = (torch.tensor([1 if r["label"] == "speak" else 0 for r in va_rows])).numpy()
    best = (-1.0, 0.0)
    for th in [float(x) for x in sorted(set(va.tolist()))]:
        m = metrics(yv, va, th)
        if m["balanced"] > best[0]: best = (m["balanced"], th)
    th = best[1]
    te = scores(te_rows, model, mu, sd)
    te = (te - te.mean()) / (te.std() + 1e-6)
    yt = (torch.tensor([1 if r["label"] == "speak" else 0 for r in te_rows])).numpy()
    out = metrics(yt, te, th)
    out.update({"val_balanced": round(best[0], 4), "threshold": round(th, 4), "test_group": test_group, "val_group": val_group})
    return out


if __name__ == "__main__":
    print(train())
