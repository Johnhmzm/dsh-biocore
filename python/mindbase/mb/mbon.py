from __future__ import annotations

from typing import List

from ..noise import clip


class MBON:
    """Mushroom-body output neuron: reads the sparse KC code, learns via DAN."""

    def __init__(self, n_kc: int):
        self.w: List[float] = [0.0] * n_kc

    def predict(self, kc: List[int]) -> float:
        if not kc:
            return 0.0
        return sum(self.w[i] for i in kc) / len(kc)

    def learn(self, kc: List[int], dan: float, lr: float) -> None:
        if not kc:
            return
        for i in kc:
            self.w[i] = clip(self.w[i] + lr * dan, -1.0, 1.0)
