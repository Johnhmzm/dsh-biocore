from __future__ import annotations

import json
import random
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, List, Tuple

from .behavior import DATA

CACHE = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR" / "embed_cache_qwen3_gguf.json"


def load_fine(limit: int | None = None) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    for file in sorted(Path(DATA).rglob("*.json")):
        try:
            row = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        group = f"{row.get('task_name') or file.stem}:{file.stem}"
        for skill in row.get("skill_annotation") or []:
            desc = " ".join(str(x) for x in (skill.get("skill_description") or []))
            objects = " ".join(str(x) for x in (skill.get("object_id") or []))
            frames = skill.get("frame_duration") or [0, 0]
            event = f"{row.get('task_name')}; {desc}; objects: {objects}; frames: {frames[0]}-{frames[1]}"
            out.append((group, event, desc))
            if limit and len(out) >= limit:
                return out
    return out


def train(epochs: int = 30) -> dict:
    import torch
    import torch.nn as nn

    rows = load_fine()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    rows = [r for r in rows if r[1] in cache]
    labels = sorted({r[2] for r in rows})
    label_index = {l: i for i, l in enumerate(labels)}
    groups: "OrderedDict[str, List]" = OrderedDict()
    for row in rows:
        groups.setdefault(row[0], []).append(row)
    keys = list(groups); random.Random(0).shuffle(keys)
    cut = int(len(keys) * 0.75)
    train_keys = set(keys[:cut])
    train = [r for k in keys[:cut] for r in groups[k]]
    test = [r for k in keys[cut:] for r in groups[k]]

    def tensor(rows):
        X = torch.tensor([cache[r[1]] for r in rows])
        y = torch.tensor([label_index[r[2]] for r in rows])
        return X, y

    n_in = len(next(iter(cache.values())))
    model = nn.Sequential(nn.Linear(n_in, 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, len(labels)))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    counts = Counter(label_index[r[2]] for r in train)
    w = torch.tensor([1.0 / max(1, counts.get(i, 0)) for i in range(len(labels))]); w = w / w.sum() * len(labels)
    loss_fn = nn.CrossEntropyLoss(weight=w)
    X, y = tensor(train)
    for _ in range(epochs):
        model.zero_grad(); loss = loss_fn(model(X), y); loss.backward(); opt.step()

    def metrics(rows):
        Xt, yt = tensor(rows)
        with torch.no_grad():
            p = model(Xt).argmax(-1)
        recalls = []
        for k in range(len(labels)):
            n = int((yt == k).sum())
            if n: recalls.append(int(((p == k) & (yt == k)).sum()) / n)
        return {"accuracy": round(int((p == yt).sum()) / len(rows), 4), "balanced": round(sum(recalls) / max(1, len(recalls)), 4), "n": len(rows)}

    return {"labels": len(labels), "train": metrics(train), "test": metrics(test), "label_counts": dict(Counter(r[2] for r in rows).most_common(10))}


if __name__ == "__main__":
    print(train())
