"""Quantum-probability uncertainty layer (mathematical try-out, not a claim).

We borrow the density-matrix / open-quantum-system formalism as a compact model
of uncertainty:
  - rho: Hermitian, trace 1, positive semidefinite
  - Born rule: action probabilities = Tr(rho * P_action)
  - Lindblad: dephasing/decay/noise
  - von Neumann entropy / purity / coherence: uncertainty metrics

This is a *math model* of ambiguity.  It does not claim the brain is quantum.
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np

I2 = np.eye(2, dtype=complex)
SX = np.array([[0, 1], [1, 0]], dtype=complex)
SY = np.array([[0, -1j], [1j, 0]], dtype=complex)
SZ = np.array([[1, 0], [0, -1]], dtype=complex)
SM = np.array([[0, 1], [0, 0]], dtype=complex)  # |0><1| lowering


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _hermitian(rho: np.ndarray) -> np.ndarray:
    return (rho + rho.conj().T) / 2.0


def _normalize(rho: np.ndarray) -> np.ndarray:
    tr = float(np.real(np.trace(rho)))
    if tr <= 1e-12:
        return I2 / 2.0
    return rho / tr


def _project_positive(rho: np.ndarray) -> np.ndarray:
    vals, vecs = np.linalg.eigh(_hermitian(rho))
    vals = np.clip(np.real(vals), 0.0, None)
    if vals.sum() <= 1e-12:
        return I2 / 2.0
    return (vecs * vals) @ vecs.conj().T


class QuantumUncertainty:
    """Two-level uncertainty state: |0>=silence, |1>=speak (or any binary gate)."""

    def __init__(self, rho: np.ndarray | None = None):
        self.rho = I2 / 2.0 if rho is None else _project_positive(_normalize(np.asarray(rho, dtype=complex)))
        self.last_actions: List[str] = []

    @classmethod
    def from_affect(cls, v: float, arousal: float, stress: float = 0.0) -> "QuantumUncertainty":
        # Bloch vector: z ~ valence, x ~ (1-stress); keep |r| <= 1
        rz = _clip(float(v), -1.0, 1.0)
        rx = 0.55 * _clip(1.0 - float(stress), 0.0, 1.0) * (0.4 + 0.6 * _clip(float(arousal), 0.0, 1.0))
        ry = 0.15
        r = np.array([rx, ry, rz], dtype=float)
        norm = float(np.linalg.norm(r))
        if norm > 0.98:
            r = r * (0.98 / norm)
        rho = (I2 + r[0] * SX + r[1] * SY + r[2] * SZ) / 2.0
        return cls(rho)

    def bloch(self) -> np.ndarray:
        return np.array([
            float(np.real(np.trace(self.rho @ SX))),
            float(np.real(np.trace(self.rho @ SY))),
            float(np.real(np.trace(self.rho @ SZ))),
        ])

    def entropy(self) -> float:
        vals = np.clip(np.real(np.linalg.eigvalsh(self.rho)), 0.0, 1.0)
        vals = vals[vals > 1e-12]
        return float(-np.sum(vals * np.log(vals))) if len(vals) else 0.0

    def purity(self) -> float:
        return float(np.real(np.trace(self.rho @ self.rho)))

    def coherence(self) -> float:
        off = self.rho - np.diag(np.diag(self.rho))
        return float(np.sum(np.abs(off)))

    def born_probs(self) -> Dict[str, float]:
        p_silence = float(np.real(self.rho[0, 0]))
        p_speak = float(np.real(self.rho[1, 1]))
        s = max(1e-12, p_silence + p_speak)
        return {"silence": round(p_silence / s, 6), "speak": round(p_speak / s, 6)}

    def step(self, dt: float = 0.1, omega: float = 1.0, gamma: float = 0.05,
             dephase: float = 0.10) -> None:
        dt = max(0.0, float(dt))
        n = max(1, int(math.ceil(dt / 0.05)))
        h = dt / n
        H = 0.5 * omega * SX
        L = math.sqrt(max(0.0, gamma)) * SM
        Lp = math.sqrt(max(0.0, dephase) / 2.0) * SZ
        for _ in range(n):
            drho = -1j * (H @ self.rho - self.rho @ H)
            drho += L @ self.rho @ L.conj().T - 0.5 * (L.conj().T @ L @ self.rho + self.rho @ L.conj().T @ L)
            drho += Lp @ self.rho @ Lp.conj().T - 0.5 * (Lp.conj().T @ Lp @ self.rho + self.rho @ Lp.conj().T @ Lp)
            self.rho = _project_positive(_normalize(self.rho + h * drho))
        self.rho = _project_positive(_normalize(self.rho))

    def nudge(self, action: str, reward: float) -> None:
        """Small coherent rotation toward the chosen action (feedback, not collapse)."""
        theta = 0.25 * float(reward)
        if action == "silence":
            theta = -theta
        U = np.cos(theta / 2.0) * I2 - 1j * np.sin(theta / 2.0) * SY
        self.rho = _project_positive(_normalize(U @ self.rho @ U.conj().T))

    def sample(self, rng) -> str:
        p = self.born_probs()
        action = "speak" if rng.random() < float(p["speak"]) else "silence"
        self.last_actions.append(action)
        return action

    def metrics(self) -> Dict[str, float]:
        p = self.born_probs()
        return {
            "entropy": round(self.entropy(), 5),
            "purity": round(self.purity(), 5),
            "coherence": round(self.coherence(), 5),
            "p_silence": p["silence"],
            "p_speak": p["speak"],
            "bloch_norm": round(float(np.linalg.norm(self.bloch())), 5),
        }
