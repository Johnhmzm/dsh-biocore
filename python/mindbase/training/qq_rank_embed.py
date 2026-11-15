from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import torch
import torch.nn as nn

from ..embedding import EmbeddingClient
from .qq_rank import feat, pairs

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"
CACHE = Path(__file__).resolve().parent.parent / "data" / "qq_context_embed_cache.json"


def key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def embed_contexts(rows, cache):
    client = EmbeddingClient.from_local_gguf(); client.timeout = 120
    texts = []
    for r in rows:
        t = "\n".join(r["context"])
        if key(t) not in cache:
            texts.append(t)
    texts = list(dict.fromkeys(texts))
    for i in range(0, len(texts), 16):
        batch = texts[i:i + 16]
        vecs = client.embed(batch)
        for t, v in zip(batch, vecs):
            cache[key(t)] = v
        if i % 160 == 0:
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
            print("embedded", i + len(batch), "/", len(texts), flush=True)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def vec(r, cache):
    return cache[key("\n".join(r["context"]))] + feat(r)


def auc(pairs_, model, mu, sd, cache):
    if not pairs_: return 0.0
    Xp = (torch.tensor([vec(p, cache) for p, _ in pairs_], dtype=torch.float32) - mu) / sd
    Xn = (torch.tensor([vec(n, cache) for _, n in pairs_], dtype=torch.float32) - mu) / sd
    with torch.no_grad():
        d = model(Xp).squeeze() - model(Xn).squeeze()
    return float(((d > 0).float() + 0.5 * (d == 0).float()).mean())


def train(epochs: int = 80) -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    groups = sorted({r["group"] for r in rows}, key=lambda g: -sum(1 for r in rows if r["group"] == g))
    test_group, val_group = groups[0], groups[1]
    train_rows = [r for r in rows if r["group"] not in (test_group, val_group)]
    val_rows = [r for r in rows if r["group"] == val_group]
    test_rows = [r for r in rows if r["group"] == test_group]
    cache = embed_contexts(rows, load_cache())
    tr, va, te = pairs(train_rows), pairs(val_rows), pairs(test_rows)
    Xp = torch.tensor([vec(p, cache) for p, _ in tr], dtype=torch.float32)
    Xn = torch.tensor([vec(n, cache) for _, n in tr], dtype=torch.float32)
    X = torch.cat([Xp, Xn], 0)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xp = (Xp - mu) / sd; Xn = (Xn - mu) / sd
    model = nn.Sequential(nn.Linear(X.shape[1], 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, 1))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    for _ in range(epochs):
        model.zero_grad()
        loss = torch.nn.functional.softplus(-(model(Xp).squeeze() - model(Xn).squeeze())).mean()
        loss.backward(); opt.step()
    return {
        "pairs_train": len(tr), "pairs_val": len(va), "pairs_test": len(te),
        "val_auc": round(auc(va, model, mu, sd, cache), 4),
        "test_auc": round(auc(te, model, mu, sd, cache), 4),
        "test_group": test_group, "val_group": val_group,
    }


if __name__ == "__main__":
    print(train())
