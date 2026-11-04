"""Connectome-constrained world model prototype.

A sparse recurrent reservoir (connectome-like wiring prior) + self-supervised
linear readout: predict the next sensory/state vector.  This is a small,
honest prototype, not a whole-brain emulation.
"""
from __future__ import annotations

import random
from typing import Dict, List, Sequence

import numpy as np


class ConnectomeWorldModel:
    def __init__(self, n_neurons: int = 128, n_input: int = 6,
                 sparsity: float = 0.08, spectral_radius: float = 0.9,
                 seed: int = 1, n_action: int = 6,
                 use_action_effects: bool = False):
        self.n = int(n_neurons)
        self.n_input = int(n_input)
        self.n_action = int(n_action)
        self.use_action_effects = bool(use_action_effects)
        self.action_dim = self.n_input if self.use_action_effects else self.n_action
        self.action_effects = np.zeros((self.n_action, self.n_input))
        self.rng = np.random.default_rng(int(seed))
        w = self.rng.normal(0.0, 1.0, size=(self.n, self.n))
        mask = self.rng.random((self.n, self.n)) < float(sparsity)
        w = w * mask
        # scale spectral radius for stable echo-state dynamics
        try:
            vals = np.linalg.eigvals(w)
            radius = float(np.max(np.abs(vals))) if len(vals) else 1.0
            w = w * (float(spectral_radius) / max(1e-9, radius))
        except Exception:
            w = w / max(1e-9, np.linalg.norm(w, 2))
        self.W = w
        self.B = self.rng.normal(0.0, 0.2, size=(self.n, self.n_input + self.action_dim))
        self.C = np.zeros((self.n, self.n_input))
        self.h = np.zeros(self.n)

    def _step(self, sensory: np.ndarray, action: int | None = None) -> np.ndarray:
        s = np.asarray(sensory, dtype=float).reshape(-1)
        if s.shape[0] != self.n_input:
            s = np.resize(s, self.n_input)
        a = np.zeros(self.action_dim)
        if action is not None:
            ai = int(action) % self.n_action
            if self.use_action_effects:
                a = self.action_effects[ai].copy()
            else:
                a[ai] = 1.0
        inp = np.concatenate([s, a])
        self.h = np.tanh(self.W @ self.h + self.B @ inp)
        return self.h

    def predict_next(self, sensory: np.ndarray, action: int | None = None) -> np.ndarray:
        h = self._step(sensory, action)
        return np.asarray(sensory, dtype=float).reshape(-1)[:self.n_input] + h @ self.C

    def fit(self, sequences: Sequence[Sequence[np.ndarray]], ridge: float = 1e-3) -> Dict[str, float]:
        X: List[np.ndarray] = []
        Y: List[np.ndarray] = []
        for seq in sequences:
            self.h = np.zeros(self.n)
            prev = None
            for s in seq:
                s = np.asarray(s, dtype=float)
                if prev is not None:
                    X.append(self.h.copy())
                    Y.append(s - prev)
                self._step(s)
                prev = s
        if not X:
            return {"n": 0, "train_mse": 0.0}
        Xm = np.asarray(X)
        Ym = np.asarray(Y)
        # ridge: C^T = (X^T X + λI)^-1 X^T Y
        A = Xm.T @ Xm + float(ridge) * np.eye(self.n)
        self.C = np.linalg.solve(A, Xm.T @ Ym)
        pred = Xm @ self.C
        return {"n": int(len(X)), "train_mse": round(float(np.mean((pred - Ym) ** 2)), 6)}

    def evaluate(self, sequences: Sequence[Sequence[np.ndarray]]) -> Dict[str, float]:
        errs = []
        base = []
        for seq in sequences:
            self.h = np.zeros(self.n)
            prev = None
            for s in seq:
                s = np.asarray(s, dtype=float)
                if prev is not None:
                    pred = prev + (self.h @ self.C)
                    errs.append(float(np.mean((pred - s) ** 2)))
                    base.append(float(np.mean((prev - s) ** 2)))
                self._step(s)
                prev = s
        return {"n": len(errs),
                "model_mse": round(float(np.mean(errs)) if errs else 0.0, 6),
                "persistence_mse": round(float(np.mean(base)) if base else 0.0, 6)}


    def fit_action(self, sequences: Sequence[Sequence[tuple]], ridge: float = 1e-3) -> Dict[str, float]:
        if self.use_action_effects:
            sums = np.zeros((self.n_action, self.n_input))
            counts = np.zeros(self.n_action)
            for seq in sequences:
                for (s0, action), (s1, _a1) in zip(seq[:-1], seq[1:]):
                    ai = int(action) % self.n_action
                    sums[ai] += np.asarray(s1, dtype=float) - np.asarray(s0, dtype=float)
                    counts[ai] += 1
            for i in range(self.n_action):
                if counts[i] > 0:
                    self.action_effects[i] = sums[i] / counts[i]
        X: List[np.ndarray] = []
        Y: List[np.ndarray] = []
        for seq in sequences:
            self.h = np.zeros(self.n)
            for (s, action), nxt in zip(seq[:-1], seq[1:]):
                s_arr = np.asarray(s, dtype=float)
                X.append(self.h.copy())
                Y.append(np.asarray(nxt[0], dtype=float) - s_arr)
                self._step(s_arr, int(action))
        if not X:
            return {"n": 0, "train_mse": 0.0}
        Xm = np.asarray(X)
        Ym = np.asarray(Y)
        A = Xm.T @ Xm + float(ridge) * np.eye(self.n)
        self.C = np.linalg.solve(A, Xm.T @ Ym)
        pred = Xm @ self.C
        return {"n": int(len(X)), "train_mse": round(float(np.mean((pred - Ym) ** 2)), 6)}

    def evaluate_action(self, sequences: Sequence[Sequence[tuple]]) -> Dict[str, float]:
        errs = []
        base = []
        for seq in sequences:
            self.h = np.zeros(self.n)
            for (s, action), nxt in zip(seq[:-1], seq[1:]):
                s_arr = np.asarray(s, dtype=float)
                nxt_arr = np.asarray(nxt[0], dtype=float)
                pred = s_arr + (self.h @ self.C)
                errs.append(float(np.mean((pred - nxt_arr) ** 2)))
                base.append(float(np.mean((s_arr - nxt_arr) ** 2)))
                self._step(s_arr, int(action))
        return {"n": len(errs),
                "model_mse": round(float(np.mean(errs)) if errs else 0.0, 6),
                "persistence_mse": round(float(np.mean(base)) if base else 0.0, 6)}


def sequences_from_rows(rows: Sequence[Dict[str, object]], n_input: int = 6) -> List[np.ndarray]:
    out = []
    for r in rows:
        need = float((r.get("needs") or 0.0))
        vec = [float(r.get("energy", 0.0)), float(r.get("social", 0.0)),
               float(r.get("stress", 0.0)), float(r.get("damage", 0.0)),
               float(r.get("viability", 1.0)), need]
        out.append(np.asarray(vec[:n_input], dtype=float))
    return out
