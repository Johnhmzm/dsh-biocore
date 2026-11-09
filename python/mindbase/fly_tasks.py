from __future__ import annotations

import numpy as np

from .fly_mb import FlyMushroomBody


def associative_score(sparsity: float = 0.05, pam_lr: float = 0.01, ppl1_lr: float = 0.012,
                      apl_gain: float = 0.6, trace_decay: float = 0.90, seed: int = 1,
                      gamma: float = 1.0, alpha: float = 0.8, beta: float = 0.7,
                      pam_gamma_ratio: float = 0.60, ppl1_alpha_ratio: float = 0.50, weight_decay: float = 0.0002) -> float:
    """CS+ gets reward, CS- does not; score = approach(CS+) - approach(CS-)."""
    mb = FlyMushroomBody(seed=seed, sparsity=sparsity, lr=pam_lr, subregion_gain={"gamma": gamma, "alpha": alpha, "beta": beta},
                         pam_gamma_ratio=pam_gamma_ratio, ppl1_alpha_ratio=ppl1_alpha_ratio, weight_decay=weight_decay)
    mb.ppl1_lr = ppl1_lr
    mb.apl_gain = apl_gain
    mb.trace_decay = trace_decay
    cs_plus = np.array([0.9, 0.8, 0.0, 1.0])
    cs_minus = np.array([0.0, 0.1, 0.9, 1.0])
    before = mb.step(cs_plus)["mbon_approach"]
    for _ in range(20):
        mb.step(cs_plus, reward=0.8)
        mb.step(cs_minus)
    after = mb.step(cs_plus)["mbon_approach"]
    return round(float(after - before), 4)


def second_order_retention(seed: int = 1, trials: int = 10) -> dict:
    import numpy as np
    cs1 = np.array([0.9, 0.8, 0.0, 1.0])
    cs2 = np.array([0.0, 0.9, 0.1, 1.0])
    mb = FlyMushroomBody(seed=seed)
    before = mb.step(cs2)["mbon_approach"]
    mb.second_order_pair(cs2, cs1, reward=1.0, repeats=3)
    after = mb.step(cs2)["mbon_approach"]
    for _ in range(max(0, int(trials))):
        mb.step(cs2)
    retained = mb.step(cs2)["mbon_approach"]
    return {
        "before": round(float(before), 4),
        "after": round(float(after), 4),
        "retained": round(float(retained), 4),
        "gain": round(float(after - before), 4),
        "retention": round(float(retained - before), 4),
    }
