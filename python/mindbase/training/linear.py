from __future__ import annotations

import math
import random
from typing import Dict, List, Sequence, Tuple

import numpy as np


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def softmax(z: Sequence[float]) -> List[float]:
    m = max(z)
    ex = [math.exp(v - m) for v in z]
    s = sum(ex)
    return [v / s for v in ex]


class SoftmaxRegression:
    """Tiny multinomial logistic regression (SGD, pure Python)."""

    def __init__(self, n_features: int, n_classes: int, lr: float = 0.15):
        self.n_features = n_features
        self.n_classes = n_classes
        self.lr = lr
        self.W = [[0.0] * n_features for _ in range(n_classes)]
        self.b = [0.0] * n_classes

    def predict_proba(self, x: Sequence[float]) -> List[float]:
        return softmax([dot(self.W[k], x) + self.b[k] for k in range(self.n_classes)])

    def predict(self, x: Sequence[float]) -> int:
        p = self.predict_proba(x)
        return max(range(self.n_classes), key=lambda i: p[i])

    def train_step(self, x: Sequence[float], y: int) -> float:
        p = self.predict_proba(x)
        for k in range(self.n_classes):
            g = p[k] - (1.0 if k == y else 0.0)
            for j in range(self.n_features):
                self.W[k][j] -= self.lr * g * x[j]
            self.b[k] -= self.lr * g
        return -math.log(max(1e-9, p[y]))

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int], epochs: int = 200, seed: int = 0) -> "SoftmaxRegression":
        rng = random.Random(seed)
        order = list(range(len(X)))
        for _ in range(epochs):
            rng.shuffle(order)
            for i in order:
                self.train_step(X[i], y[i])
        return self

    def accuracy(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> float:
        if not y:
            return 0.0
        return sum(1 for x, t in zip(X, y) if self.predict(x) == t) / len(y)

    def to_dict(self) -> Dict[str, object]:
        return {"n_features": self.n_features, "n_classes": self.n_classes, "lr": self.lr, "W": self.W, "b": self.b}

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "SoftmaxRegression":
        obj = cls(int(d["n_features"]), int(d["n_classes"]), float(d["lr"]))
        obj.W = [list(row) for row in d["W"]]  # type: ignore[arg-type]
        obj.b = list(d["b"])  # type: ignore[arg-type]
        return obj


class LinearRegressor:
    """Tiny multi-output linear regression (SGD, pure Python)."""

    def __init__(self, n_features: int, n_outputs: int, lr: float = 0.05):
        self.n_features = n_features
        self.n_outputs = n_outputs
        self.lr = lr
        self.W = [[0.0] * n_features for _ in range(n_outputs)]
        self.b = [0.0] * n_outputs

    def predict(self, x: Sequence[float]) -> List[float]:
        return [dot(self.W[k], x) + self.b[k] for k in range(self.n_outputs)]

    def train_step(self, x: Sequence[float], y: Sequence[float]) -> float:
        pred = self.predict(x)
        err = [pred[k] - y[k] for k in range(self.n_outputs)]
        for k in range(self.n_outputs):
            for j in range(self.n_features):
                self.W[k][j] -= self.lr * err[k] * x[j]
            self.b[k] -= self.lr * err[k]
        return sum(e * e for e in err) / max(1, len(err))

    def fit(self, X: Sequence[Sequence[float]], Y: Sequence[Sequence[float]], epochs: int = 200) -> "LinearRegressor":
        for _ in range(epochs):
            for x, y in zip(X, Y):
                self.train_step(x, y)
        return self

    def loss(self, X: Sequence[Sequence[float]], Y: Sequence[Sequence[float]]) -> float:
        if not X:
            return 0.0
        total = 0.0
        for x, y in zip(X, Y):
            p = self.predict(x)
            total += sum((a - b) ** 2 for a, b in zip(p, y)) / max(1, len(y))
        return total / len(X)

    def to_dict(self) -> Dict[str, object]:
        return {"n_features": self.n_features, "n_outputs": self.n_outputs, "lr": self.lr, "W": self.W, "b": self.b}

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "LinearRegressor":
        obj = cls(int(d["n_features"]), int(d["n_outputs"]), float(d["lr"]))
        obj.W = [list(row) for row in d["W"]]  # type: ignore[arg-type]
        obj.b = list(d["b"])  # type: ignore[arg-type]
        return obj


"""Closed-form ridge regression.

A small math-tool transfer: replace SGD least squares with a standardized
ridge solution.  Useful when tabular features are sparse/correlated and the
training set is small.
"""

from typing import Dict, List, Sequence

import numpy as np


class RidgeRegressor:
    def __init__(self, n_features: int, n_outputs: int, lr: float = 0.05, alpha: float = 1.0):
        self.n_features = int(n_features)
        self.n_outputs = int(n_outputs)
        self.lr = float(lr)
        self.alpha = float(alpha)
        self.mean = np.zeros(self.n_features)
        self.std = np.ones(self.n_features)
        self.W = np.zeros((self.n_features + 1, self.n_outputs))
        self._fitted = False

    def fit(self, X: Sequence[Sequence[float]], Y: Sequence[Sequence[float]], epochs: int = 0) -> "RidgeRegressor":
        Xa = np.asarray(X, dtype=float)
        Ya = np.asarray(Y, dtype=float)
        if Xa.size == 0 or Ya.size == 0:
            return self
        if Xa.ndim == 1:
            Xa = Xa.reshape(1, -1)
        if Ya.ndim == 1:
            Ya = Ya.reshape(-1, 1)
        self.mean = Xa.mean(axis=0)
        self.std = Xa.std(axis=0)
        self.std[self.std < 1e-8] = 1.0
        Z = (Xa - self.mean) / self.std
        Zb = np.hstack([Z, np.ones((Z.shape[0], 1))])
        reg = self.alpha * np.eye(Zb.shape[1])
        reg[-1, -1] = 0.0  # do not penalize intercept
        self.W = np.linalg.solve(Zb.T @ Zb + reg, Zb.T @ Ya)
        self._fitted = True
        return self

    def predict(self, x: Sequence[float]) -> List[float]:
        X = np.asarray(x, dtype=float)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        Z = (X - self.mean) / self.std
        Zb = np.hstack([Z, np.ones((Z.shape[0], 1))])
        return list((Zb @ self.W).ravel())

    def loss(self, X: Sequence[Sequence[float]], Y: Sequence[Sequence[float]]) -> float:
        Xa = np.asarray(X, dtype=float)
        Ya = np.asarray(Y, dtype=float)
        if Xa.size == 0 or Ya.size == 0:
            return 0.0
        pred = np.asarray([self.predict(x) for x in Xa], dtype=float)
        return float(np.mean((pred - Ya) ** 2))

    def to_dict(self) -> Dict[str, object]:
        return {
            "kind": "ridge",
            "n_features": self.n_features,
            "n_outputs": self.n_outputs,
            "lr": self.lr,
            "alpha": self.alpha,
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
            "W": self.W.tolist(),
            "fitted": self._fitted,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "RidgeRegressor":
        obj = cls(int(data["n_features"]), int(data["n_outputs"]), float(data.get("lr", 0.05)), float(data.get("alpha", 1.0)))
        obj.mean = np.asarray(data.get("mean", []), dtype=float)
        obj.std = np.asarray(data.get("std", []), dtype=float)
        obj.W = np.asarray(data.get("W", []), dtype=float)
        obj._fitted = bool(data.get("fitted", True))
        return obj
