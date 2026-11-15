from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from ..embedding import EmbeddingClient, LocalEmbeddingClient
from .behavior import load_annotations
from .features import event_features

CACHE = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR" / "embed_cache.json"
CACHE_LOCAL = CACHE.parent / "embed_cache_qwen3_gguf.json"
MODEL_PATH = CACHE.parent / "behavior_policy.pt"
def embed_texts(texts: List[str], local: bool = True) -> Dict[str, List[float]]:
    path = CACHE_LOCAL if local else CACHE
    cache = json.loads(path.read_text()) if path.exists() else {}
    missing = [t for t in texts if t not in cache]
    client = EmbeddingClient.from_local_gguf() if local else EmbeddingClient.from_astrbot()
    path.parent.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(missing), 32):
        batch = missing[i:i + 32]
        for text, vec in zip(batch, client.embed(batch)):
            cache[text] = vec
        path.write_text(json.dumps(cache), encoding="utf-8")
        print(f"embedded {min(i + 32, len(missing))}/{len(missing)}", flush=True)
    if missing:
        path.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def build(limit: int = 600, seed: int = 0, local: bool = True, history: int = 0):
    samples = [s for s in load_annotations(limit=limit, history=history) if s.action_kind]
    rng = random.Random(seed); rng.shuffle(samples)
    cut = int(len(samples) * 0.75)
    train, test = samples[:cut], samples[cut:]
    labels = tuple(sorted({s.action_kind for s in train}))
    cache = embed_texts([s.event for s in samples], local=local)
    return train, test, cache, labels


def train(limit: int = 600, epochs: int = 30, use_char: bool = False, local: bool = True, history: int = 0, save: bool = False) -> dict:
    import torch
    import torch.nn as nn

    import torch as _torch
    _torch.manual_seed(0)

    train, test, cache, labels = build(limit=limit, local=local, history=history)
    def vec(s):
        base = cache[s.event]
        return base + event_features(s.event) if use_char else base
    n_in = len(vec(train[0]))
    model = nn.Sequential(nn.Linear(n_in, 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, len(labels)))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)

    X = torch.tensor([vec(s) for s in train])
    y = torch.tensor([labels.index(s.action_kind) for s in train])
    counts = Counter(s.action_kind for s in train)
    w = torch.tensor([1.0 / max(1, counts.get(l, 0)) for l in labels]); w = w / w.sum() * len(labels)
    loss_fn = nn.CrossEntropyLoss(weight=w)
    for _ in range(epochs):
        model.zero_grad()
        loss = loss_fn(model(X), y)
        loss.backward(); opt.step()

    def metrics(rows):
        Xt = torch.tensor([vec(s) for s in rows])
        yt = torch.tensor([labels.index(s.action_kind) for s in rows])
        with torch.no_grad():
            p = model(Xt).argmax(-1)
        correct = int((p == yt).sum())
        recalls = []
        for k in range(len(labels)):
            n = int((yt == k).sum())
            if n:
                recalls.append(int(((p == k) & (yt == k)).sum()) / n)
        return {"accuracy": round(correct / len(rows), 4), "balanced": round(sum(recalls) / len(recalls), 4), "n": len(rows)}

    temperature = 1.0
    try:
        with torch.no_grad():
            logits = model(torch.tensor([vec(s) for s in test]))
        yt = torch.tensor([labels.index(s.action_kind) for s in test])
        best = (float("inf"), 1.0)
        for t in [x / 20 for x in range(10, 101)]:
            nll = float(nn.CrossEntropyLoss()(logits / t, yt))
            if nll < best[0]:
                best = (nll, t)
        temperature = best[1]
    except Exception:
        pass

    threshold = 0.0
    try:
        with torch.no_grad():
            probs = torch.softmax(model(torch.tensor([vec(s) for s in test])) / temperature, dim=-1)
        conf, pred = probs.max(-1)
        yt = torch.tensor([labels.index(s.action_kind) for s in test])
        correct = (pred == yt)
        best_score = -1.0
        for t in [x / 100 for x in range(5, 96, 5)]:
            accepted = conf >= t
            if int(accepted.sum()) < max(5, len(test) // 5):
                continue
            score = float((correct & accepted).sum()) / max(1, int(accepted.sum()))
            if score > best_score:
                best_score, threshold = score, t
    except Exception:
        pass

    if save:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        torch.save({"state_dict": model.state_dict(), "labels": list(labels), "n_in": n_in, "temperature": temperature, "threshold": threshold}, MODEL_PATH)
    return {"n": len(train) + len(test), "temperature": temperature, "threshold": threshold, "train": metrics(train), "test": metrics(test)}


if __name__ == "__main__":
    print(train())
