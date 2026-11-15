from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from ..actions import ACTION_KINDS
from ..embedding import EmbeddingClient
from .alfworld import _extract_action, action_kind

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "ALFWorld" / "train_all_alfworld.jsonl"
CACHE = DATA.parent / "embed_seq_cache.json"


def load_text_sequences(path: Path | str = DATA, limit_rows: int = 80) -> List[Tuple[str, List[str], List[int]]]:
    out: List[Tuple[str, List[str], List[int]]] = []
    with Path(path).open("r", encoding="utf-8", errors="ignore") as fh:
        for i, line in enumerate(fh):
            if i >= limit_rows:
                break
            try:
                row = json.loads(line)
            except Exception:
                continue
            texts: List[str] = []
            ys: List[int] = []
            last_user = ""
            for msg in row.get("messages") or []:
                if msg.get("role") == "user":
                    last_user = str(msg.get("content") or "")
                elif msg.get("role") == "assistant":
                    action = _extract_action(str(msg.get("content") or ""))
                    if not action:
                        continue
                    texts.append(last_user[-2000:])
                    ys.append(ACTION_KINDS.index(action_kind(action)))
            if texts:
                out.append((str(row.get("file_name") or i), texts, ys))
    return out


def _embed_all(texts: List[str]) -> Dict[str, List[float]]:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    missing = [t for t in texts if t not in cache]
    client = EmbeddingClient.from_astrbot()
    for i in range(0, len(missing), 32):
        batch = missing[i:i + 32]
        for text, vec in zip(batch, client.embed(batch)):
            cache[text] = vec
    if missing:
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def train(limit_rows: int = 80, epochs: int = 20) -> dict:
    import torch
    import torch.nn as nn

    seqs = load_text_sequences(limit_rows=limit_rows)
    rng = random.Random(0); rng.shuffle(seqs)
    cut = int(len(seqs) * 0.75)
    train, test = seqs[:cut], seqs[cut:]
    cache = _embed_all([t for _, texts, _ in seqs for t in texts])
    n_in = len(next(iter(cache.values())))
    model = nn.GRU(n_in, 64, batch_first=True)
    head = nn.Linear(64, len(ACTION_KINDS))
    opt = torch.optim.Adam(list(model.parameters()) + list(head.parameters()), lr=1e-3)

    def batch(rows):
        maxlen = max(len(t) for _, t, _ in rows)
        X = torch.zeros(len(rows), maxlen, n_in)
        Y = torch.full((len(rows), maxlen), -100, dtype=torch.long)
        for i, (_, texts, ys) in enumerate(rows):
            X[i, :len(texts)] = torch.tensor([cache[t] for t in texts])
            Y[i, :len(ys)] = torch.tensor(ys)
        return X, Y

    counts = Counter(y for _, _, ys in train for y in ys)
    w = torch.tensor([1.0 / max(1, counts.get(i, 0)) for i in range(len(ACTION_KINDS))])
    w = w / w.sum() * len(ACTION_KINDS)
    loss_fn = nn.CrossEntropyLoss(ignore_index=-100, weight=w)
    X, Y = batch(train)
    for _ in range(epochs):
        model.zero_grad()
        loss = loss_fn(head(model(X)[0]).reshape(-1, len(ACTION_KINDS)), Y.reshape(-1))
        loss.backward(); opt.step()

    def metrics(rows):
        X, Y = batch(rows)
        with torch.no_grad():
            p = head(model(X)[0]).argmax(-1)
        correct = total = 0; rec = []
        for k in range(len(ACTION_KINDS)):
            tp = sum(1 for i in range(Y.size(0)) for j in range(Y.size(1)) if Y[i, j] == k and p[i, j] == k)
            n = sum(1 for i in range(Y.size(0)) for j in range(Y.size(1)) if Y[i, j] == k)
            if n: rec.append(tp / n)
            correct += tp; total += n
        return {"accuracy": round(correct / max(1, total), 4), "balanced": round(sum(rec) / max(1, len(rec)), 4), "n": total}

    return {"seqs": len(seqs), "train": metrics(train), "test": metrics(test)}


if __name__ == "__main__":
    print(train())
