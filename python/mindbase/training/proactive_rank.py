from __future__ import annotations

import json
from pathlib import Path

import torch
import torch.nn as nn

from .qq_rank import feat, pairs

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def proactive_pairs(rows):
    sub = [r for r in rows if int(r.get("target_at", 0)) == 0]
    return pairs(sub)


def fit(tr, epochs=250):
    Xp = torch.tensor([feat(p) for p, _ in tr], dtype=torch.float32)
    Xn = torch.tensor([feat(n) for _, n in tr], dtype=torch.float32)
    X = torch.cat([Xp, Xn], 0); mu, sd = X.mean(0), X.std(0) + 1e-6
    Xp = (Xp - mu) / sd; Xn = (Xn - mu) / sd
    model = nn.Sequential(nn.Linear(X.shape[1], 16), nn.ReLU(), nn.Linear(16, 1))
    opt = torch.optim.Adam(model.parameters(), lr=0.02)
    for _ in range(epochs):
        model.zero_grad()
        loss = torch.nn.functional.softplus(-(model(Xp).squeeze() - model(Xn).squeeze())).mean()
        loss.backward(); opt.step()
    return model, mu, sd


def auc(ps, model, mu, sd):
    if not ps: return 0.0
    Xp = (torch.tensor([feat(p) for p, _ in ps], dtype=torch.float32) - mu) / sd
    Xn = (torch.tensor([feat(n) for _, n in ps], dtype=torch.float32) - mu) / sd
    with torch.no_grad(): d = model(Xp).squeeze() - model(Xn).squeeze()
    return float(((d > 0).float() + 0.5 * (d == 0).float()).mean())


def run():
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    tr = proactive_pairs([r for r in rows if r["group"] not in (test_group, val_group)])
    va = proactive_pairs([r for r in rows if r["group"] == val_group])
    te = proactive_pairs([r for r in rows if r["group"] == test_group])
    model, mu, sd = fit(tr)
    return {"train_pairs": len(tr), "val_pairs": len(va), "test_pairs": len(te), "val_auc": round(auc(va, model, mu, sd), 4), "test_auc": round(auc(te, model, mu, sd), 4), "test_group": test_group}


if __name__ == "__main__":
    print(run())
