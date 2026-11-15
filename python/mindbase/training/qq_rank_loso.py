from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import torch
import torch.nn as nn

from .qq_rank import feat, pairs

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def fit(train_pairs, epochs=200):
    Xp = torch.tensor([feat(p) for p, _ in train_pairs], dtype=torch.float32)
    Xn = torch.tensor([feat(n) for _, n in train_pairs], dtype=torch.float32)
    X = torch.cat([Xp, Xn], 0)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xp = (Xp - mu) / sd; Xn = (Xn - mu) / sd
    model = nn.Sequential(nn.Linear(X.shape[1], 1))
    opt = torch.optim.Adam(model.parameters(), lr=0.02)
    for _ in range(epochs):
        model.zero_grad()
        loss = torch.nn.functional.softplus(-(model(Xp).squeeze() - model(Xn).squeeze())).mean()
        loss.backward(); opt.step()
    return model, mu, sd


def auc(pairs_, model, mu, sd):
    if not pairs_: return 0.0
    Xp = (torch.tensor([feat(p) for p, _ in pairs_], dtype=torch.float32) - mu) / sd
    Xn = (torch.tensor([feat(n) for _, n in pairs_], dtype=torch.float32) - mu) / sd
    with torch.no_grad():
        d = model(Xp).squeeze() - model(Xn).squeeze()
    return float(((d > 0).float() + 0.5 * (d == 0).float()).mean())


def run() -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    byg = defaultdict(list)
    for r in rows: byg[r["group"]].append(r)
    groups = [g for g, v in sorted(byg.items(), key=lambda kv: -len(kv[1])) if len(pairs(v)) >= 20]
    res = {}
    for g in groups:
        tr = pairs([r for x, v in byg.items() if x != g for r in v])
        te = pairs(byg[g])
        if len(tr) < 100 or len(te) < 10: continue
        model, mu, sd = fit(tr)
        res[g] = {"n": len(te), "auc": round(auc(te, model, mu, sd), 4)}
    vals = [v["auc"] for v in res.values()]
    return {"groups": len(res), "mean_auc": round(sum(vals)/max(1,len(vals)),4), "results": res}


if __name__ == "__main__":
    import json as j
    print(j.dumps(run(), ensure_ascii=False, indent=2))
