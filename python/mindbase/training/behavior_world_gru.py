from __future__ import annotations

import random
import numpy as np

from .behavior_world_full import load_all


def train_gru(epochs: int = 20, window: int = 32, batch: int = 64) -> dict:
    import torch
    import torch.nn as nn

    episodes = load_all()
    train_eps, test_eps = episodes[:-1], episodes[-1:]
    states = np.concatenate([e[0] for e in train_eps])
    actions = np.concatenate([e[1] for e in train_eps])
    X = np.concatenate([states, actions], axis=1)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    delta = np.concatenate([e[2] - e[0] for e in train_eps])
    dmu, dsd = delta.mean(0), delta.std(0) + 1e-6

    seqs = []
    for s, a, ns, r in train_eps:
        x = (np.concatenate([s, a], 1) - mu) / sd
        y = np.concatenate([(ns - s - dmu) / dsd, r[:, None] * 10.0], axis=1)
        seqs.append((x, y))

    model = nn.GRU(X.shape[1], 128, batch_first=True)
    head = nn.Linear(128, 257)
    opt = torch.optim.Adam(list(model.parameters()) + list(head.parameters()), lr=1e-3)
    loss_fn = nn.MSELoss()
    rng = random.Random(0)
    for _ in range(epochs):
        for _ in range(50):
            xs, ys = [], []
            for _ in range(batch):
                x, y = rng.choice(seqs)
                start = rng.randrange(0, max(1, len(x) - window))
                xs.append(torch.tensor(x[start:start + window], dtype=torch.float32))
                ys.append(torch.tensor(y[start:start + window], dtype=torch.float32))
            xb = torch.stack(xs); yb = torch.stack(ys)
            model.zero_grad()
            loss = loss_fn(head(model(xb)[0]), yb)
            loss.backward(); opt.step()

    maes, bases, rmaes = [], [], []
    for s, a, ns, r in test_eps:
        x = torch.tensor((np.concatenate([s, a], 1) - mu) / sd, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            h, _ = model(x)
            pred = head(h)[0].numpy()
        pred_next = s + pred[:, :256] * dsd + dmu
        maes.append(np.abs(pred_next - ns).mean())
        bases.append(np.abs(s - ns).mean())
        rmaes.append(np.abs(pred[:, 256] / 10.0 - r).mean())
    return {
        "episodes": len(episodes),
        "train": len(states),
        "test": sum(len(e[0]) for e in test_eps),
        "state_mae": round(float(np.mean(maes)), 5),
        "persistence_mae": round(float(np.mean(bases)), 5),
        "reward_mae": round(float(np.mean(rmaes)), 5),
    }


if __name__ == "__main__":
    print(train_gru())
