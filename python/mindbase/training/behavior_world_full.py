from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR"


def load_arrays(parquet_path: Path, annotation_path: Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    df = pd.read_parquet(parquet_path)
    ann = json.loads(annotation_path.read_text(encoding="utf-8"))
    skills = ann.get("skill_annotation") or []
    states = np.stack([np.asarray(v, dtype=np.float32) for v in df["observation.state"]])
    actions = np.stack([np.asarray(v, dtype=np.float32) for v in df["action"]])
    n = len(df)
    rewards = np.zeros(n, dtype=np.float32)
    # shaped reward: progress through ordered skills; final skill completion = 1
    for i, row in enumerate(df.itertuples()):
        idx = next((k for k, s in enumerate(skills) if s.get("frame_duration") and s["frame_duration"][0] <= i < s["frame_duration"][1]), None)
        if idx is None:
            continue
        start, end = skills[idx]["frame_duration"]
        progress = (idx + (i - start) / max(1, end - start)) / max(1, len(skills))
        prev = rewards[i - 1] if i > 0 else 0.0
        # store cumulative progress in rewards for now
        rewards[i] = progress
    # convert cumulative progress to per-step reward
    rewards = np.diff(np.concatenate([[0.0], rewards])).astype(np.float32)
    returns = rewards.copy()
    next_states = np.concatenate([states[1:], states[-1:]], axis=0)
    return states, actions, next_states, returns


def load_all():
    episodes = []
    for p in sorted(DATA.glob("episode_*.parquet")):
        a = DATA / "annotations" / "task-0000" / (p.stem + ".json")
        if a.exists() and a.stat().st_size > 100:
            try:
                episodes.append(load_arrays(p, a))
            except Exception:
                continue
    return episodes


def train(epochs: int = 50) -> dict:
    import torch
    import torch.nn as nn

    episodes = load_all()
    train_eps, test_eps = episodes[:-1], episodes[-1:]
    states = np.concatenate([e[0] for e in train_eps])
    actions = np.concatenate([e[1] for e in train_eps])
    next_states = np.concatenate([e[2] for e in train_eps])
    rewards = np.concatenate([e[3] for e in train_eps])
    X = np.concatenate([states, actions], axis=1)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Xn = (X - mu) / sd
    delta = next_states - states
    dmu, dsd = delta.mean(0), delta.std(0) + 1e-6
    Y = np.concatenate([(delta - dmu) / dsd, rewards[:, None] * 10.0], axis=1)

    xt = torch.tensor(Xn, dtype=torch.float32)
    yt = torch.tensor(Y, dtype=torch.float32)
    model = nn.Sequential(nn.Linear(X.shape[1], 256), nn.ReLU(), nn.Linear(256, 257))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()
    for _ in range(epochs):
        model.zero_grad(); loss = loss_fn(model(xt), yt); loss.backward(); opt.step()

    with torch.no_grad():
        preds = [model(torch.tensor(((np.concatenate([s, a], 1) - mu) / sd), dtype=torch.float32)).numpy() for s, a, _, _ in test_eps]
    maes, bases, rmaes = [], [], []
    for pred, (s, a, ns, r) in zip(preds, test_eps):
        pred_delta = pred[:, :256] * dsd + dmu
        pred_next = s + pred_delta
        maes.append(np.abs(pred_next - ns).mean())
        bases.append(np.abs(s - ns).mean())
        rmaes.append(np.abs(pred[:, 256] / 10.0 - r).mean())
    return {
        "episodes": len(episodes),
        "n_train": len(states),
        "n_test": sum(len(e[0]) for e in test_eps),
        "state_dim": 256,
        "action_dim": 23,
        "test_state_mae": round(float(np.mean(maes)), 5),
        "test_persistence_mae": round(float(np.mean(bases)), 5),
        "test_reward_mae": round(float(np.mean(rmaes)), 5),
    }


if __name__ == "__main__":
    print(train())
