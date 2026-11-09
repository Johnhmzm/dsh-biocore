"""Learned value model for predicted states/actions.

Trained on trajectories: value(state, action) = average future needs over H
steps.  A simple ridge readout; used by the policy instead of a hand-weighted
predicted-state score.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np


class ValueModel:
    def __init__(self, n_state: int = 6, n_action: int = 6, horizon: int = 5):
        self.n_state = int(n_state)
        self.n_action = int(n_action)
        self.horizon = int(horizon)
        self.w = np.zeros(self.n_state + self.n_action + 1)

    def _x(self, state, action: int) -> np.ndarray:
        s = np.asarray(state, dtype=float).reshape(-1)[:self.n_state]
        a = np.zeros(self.n_action)
        a[int(action) % self.n_action] = 1.0
        return np.concatenate([s, a, [1.0]])

    def fit_targets(self, sequences: Sequence[Sequence[tuple]], targets: Sequence[float],
                    ridge: float = 1e-3) -> Dict[str, float]:
        X: List[np.ndarray] = []
        Y: List[float] = []
        for seq, target in zip(sequences, targets):
            for state, action in seq[:-1]:
                X.append(self._x(state, int(action)))
                Y.append(float(target))
        if not X:
            return {"n": 0, "mse": 0.0}
        Xm = np.asarray(X)
        Ym = np.asarray(Y)
        A = Xm.T @ Xm + float(ridge) * np.eye(Xm.shape[1])
        self.w = np.linalg.solve(A, Xm.T @ Ym)
        pred = Xm @ self.w
        return {"n": int(len(X)), "mse": round(float(np.mean((pred - Ym) ** 2)), 6)}

    def fit(self, sequences: Sequence[Sequence[tuple]], ridge: float = 1e-3) -> Dict[str, float]:
        X: List[np.ndarray] = []
        Y: List[float] = []
        for seq in sequences:
            for t, (state, action) in enumerate(seq[:-1]):
                future = [float(x[0][5]) for x in seq[t + 1:t + 1 + self.horizon]]
                if not future:
                    continue
                X.append(self._x(state, int(action)))
                Y.append(sum(future) / len(future))
        if not X:
            return {"n": 0, "mse": 0.0}
        Xm = np.asarray(X)
        Ym = np.asarray(Y)
        A = Xm.T @ Xm + float(ridge) * np.eye(Xm.shape[1])
        self.w = np.linalg.solve(A, Xm.T @ Ym)
        pred = Xm @ self.w
        return {"n": int(len(X)), "mse": round(float(np.mean((pred - Ym) ** 2)), 6)}

    def predict(self, state, action: int) -> float:
        return float(self._x(state, action) @ self.w)
