from __future__ import annotations

import json
import math
from pathlib import Path

import torch
import torch.nn as nn

DATA = Path(__file__).resolve().parent.parent / "data" / "human_addressed.jsonl"


def feat(r):
    hour = float(r.get("hour", 0))
    return [
        float(r.get("target_at", 1)),
        math.log1p(float(r.get("target_recent", 0))),
        min(float(r.get("target_gap", 9999)), 3600) / 3600.0,
        math.log1p(float(r.get("group_recent", 0))),
        min(float(r.get("since_last", 9999)), 3600) / 3600.0,
        float(r.get("group_size", 0)) / 100.0,
        math.sin(2 * math.pi * hour),
        math.cos(2 * math.pi * hour),
        float(r.get("message_len", 0)),
        float(r.get("is_question", 0)),
        float(r.get("target_reciprocity", 0)),
    ]


def auc(y, s):
    pos = [v for v, t in zip(s, y) if t == 1]
    neg = [v for v, t in zip(s, y) if t == 0]
    if not pos or not neg: return 0.0
    return sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg) / (len(pos) * len(neg))


def metrics(y, s, th):
    p = [1 if v >= th else 0 for v in s]
    rec = []
    for k in (0, 1):
        n = sum(1 for t in y if t == k)
        if n: rec.append(sum(1 for pi, ti in zip(p, y) if pi == k and ti == k) / n)
    return {"accuracy": round(sum(1 for pi, ti in zip(p, y) if pi == ti) / len(y), 4), "balanced": round(sum(rec) / max(1, len(rec)), 4), "speak_pred": sum(p), "n": len(y)}


def run():
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    tr = [r for r in rows if r["group"] not in (test_group, val_group)]
    va = [r for r in rows if r["group"] == val_group]
    te = [r for r in rows if r["group"] == test_group]
    X = torch.tensor([feat(r) for r in tr]); y = torch.tensor([1 if r["label"] == "speak" else 0 for r in tr])
    mu, sd = X.mean(0), X.std(0) + 1e-6; X = (X - mu) / sd
    model = nn.Sequential(nn.Linear(X.shape[1], 16), nn.ReLU(), nn.Linear(16, 1))
    opt = torch.optim.Adam(model.parameters(), lr=0.02)
    for _ in range(300):
        model.zero_grad()
        loss = nn.functional.binary_cross_entropy_with_logits(model(X).squeeze(), y.float(), pos_weight=torch.tensor([2.0]))
        loss.backward(); opt.step()
    def score(rs):
        Z = (torch.tensor([feat(r) for r in rs]) - mu) / sd
        with torch.no_grad(): return model(Z).squeeze().numpy()
    sv = score(va); yv = [1 if r["label"] == "speak" else 0 for r in va]
    best = (-1.0, 0.0)
    for th in sorted(set(sv.tolist())):
        m = metrics(yv, sv, th)
        if m["balanced"] > best[0]: best = (m["balanced"], th)
    st = score(te); yt = [1 if r["label"] == "speak" else 0 for r in te]
    out = metrics(yt, st, best[1])
    out.update({"val_balanced": round(best[0], 4), "test_auc": round(auc(yt, st), 4), "train": len(tr), "test": len(te), "test_group": test_group, "val_group": val_group})
    return out


if __name__ == "__main__":
    print(run())
