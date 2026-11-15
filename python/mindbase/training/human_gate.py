from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List, Tuple

import pandas as pd

from ..embedding import EmbeddingClient

CPED = Path(__file__).resolve().parent.parent / "data" / "real" / "CPED" / "valid_split.csv"
ESCONV = Path(__file__).resolve().parent.parent / "data" / "real" / "ESConv" / "ESConv.json"
DEFAULT = Path(__file__).resolve().parent.parent / "data" / "human_gate.pt"


def _cped(limit: int) -> List[Tuple[str, str, int]]:
    df = pd.read_csv(CPED).dropna(subset=["Dialogue_ID", "Utterance_ID", "Utterance", "Speaker"])
    rows = []
    for did, g in df.groupby("Dialogue_ID"):
        g = g.sort_values("Utterance_ID")
        texts = [str(x) for x in g["Utterance"]]
        speakers = [str(x) for x in g["Speaker"]]
        if len(texts) < 3:
            continue
        target = speakers[0]
        for i in range(len(texts) - 1):
            context = "\n".join(f"{speakers[j]}: {texts[j][:120]}" for j in range(max(0, i - 4), i + 1))
            rows.append(("cped:" + str(did), context, 1 if speakers[i + 1] == target else 0))
    random.Random(11).shuffle(rows)
    return rows[:limit]


def _esconv(limit: int) -> List[Tuple[str, str, int]]:
    data = json.loads(ESCONV.read_text(encoding="utf-8"))
    rows = []
    for i, d in enumerate(data):
        dialog = d.get("dialog") or []
        if len(dialog) < 3:
            continue
        for j in range(len(dialog) - 1):
            ctx = dialog[max(0, j - 4):j + 1]
            context = "\n".join(f"{x.get('speaker')}: {x.get('content', '').strip()[:120]}" for x in ctx)
            label = 1 if dialog[j + 1].get("speaker") == "supporter" else 0
            rows.append((f"esconv:{i}", context, label))
    random.Random(12).shuffle(rows)
    return rows[:limit]


def pairs(limit_per_source: int = 1500, seed: int = 0) -> List[Tuple[str, str, int, str]]:
    rows = [(g, c, y, "cped") for g, c, y in _cped(limit_per_source)]
    rows += [(g, c, y, "esconv") for g, c, y in _esconv(limit_per_source)]
    random.Random(seed).shuffle(rows)
    return rows


def train(limit_per_source: int = 1500, epochs: int = 80) -> dict:
    import torch
    import torch.nn as nn

    rows = pairs(limit_per_source)
    groups = sorted({r[0] for r in rows})
    random.Random(1).shuffle(groups)
    cut = int(len(groups) * 0.8)
    train_groups = set(groups[:cut])
    train = [r for r in rows if r[0] in train_groups]
    test = [r for r in rows if r[0] not in train_groups]
    client = EmbeddingClient.from_local_gguf()
    client.timeout = 120

    def emb(rs):
        return torch.tensor(client.embed([r[1] for r in rs]))

    X, y = emb(train), torch.tensor([r[2] for r in train])
    Xt, yt = emb(test), torch.tensor([r[2] for r in test])
    model = nn.Sequential(nn.Linear(X.shape[1], 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, 2))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    w = torch.tensor([1.0 / max(1, int((y == k).sum())) for k in (0, 1)]); w = w / w.sum() * 2
    loss_fn = nn.CrossEntropyLoss(weight=w)
    for _ in range(epochs):
        model.zero_grad(); loss = loss_fn(model(X), y); loss.backward(); opt.step()

    def metrics(rs, X_, y_):
        with torch.no_grad():
            p = model(X_).argmax(-1)
        recalls = []
        for k in (0, 1):
            n = int((y_ == k).sum())
            if n: recalls.append(int(((p == k) & (y_ == k)).sum()) / n)
        out = {
            "n": len(rs),
            "accuracy": round(float((p == y_).float().mean()), 4),
            "balanced": round(sum(recalls) / max(1, len(recalls)), 4),
        }
        for src in ("cped", "esconv"):
            idx = [i for i, r in enumerate(rs) if r[3] == src]
            if not idx:
                continue
            ss = torch.tensor([r[2] for r in rs if r[3] == src])
            pp = p[idx]
            rr = []
            for k in (0, 1):
                n = int((ss == k).sum())
                if n: rr.append(int(((pp == k) & (ss == k)).sum()) / n)
            out[src] = {"n": len(idx), "balanced": round(sum(rr) / max(1, len(rr)), 4)}
        return out

    print({"train": metrics(train, X, y), "test": metrics(test, Xt, yt)}, flush=True)
    return {"train": metrics(train, X, y), "test": metrics(test, Xt, yt)}


if __name__ == "__main__":
    print(train())
