from __future__ import annotations

from typing import Dict

from .noise import clip


class OnlineIterator:
    """Per-turn online update of a user's numeric delta. No fixed 0.2 reward."""

    def __init__(self, profile_store, user_id: str, lr: float = 0.02):
        self.store = profile_store
        self.user_id = str(user_id)
        self.lr = float(lr)
        self.baseline = 0.50
        self.var = 1.0
        meta = profile_store.load_meta(self.user_id)
        self.age = int(meta.get("age", 0))
        if int(meta.get("version", 1)) < 2:
            self.n = 0
            self.mean: Dict[str, float] = {}
        else:
            self.n = int(meta.get("n", 0))
            self.mean = dict(meta.get("mean", {}) or {})
        self.delta: Dict[str, float] = dict(profile_store.load_delta(self.user_id))

    def turn_reward(self, trust_before: float, trust_after: float,
                    identity_before: float, identity_after: float,
                    prediction_error: float, viability: float, stress: float) -> float:
        return (
            2.0 * (trust_after - trust_before)
            + 1.0 * (identity_after - identity_before)
            + 0.5 * (1.0 - clip(float(prediction_error), 0.0, 1.0))
            + 0.2 * (clip(float(viability), 0.0, 1.0) - 0.5)
            - 0.2 * clip(float(stress), 0.0, 1.0)
        )

    def update(self, mind, reward: float, novelty: float = 1.0) -> Dict[str, float]:
        raw = float(reward)
        advantage = raw - self.baseline
        self.var = 0.95 * self.var + 0.05 * advantage * advantage
        norm = advantage / ((self.var ** 0.5) + 1e-6)
        self.baseline = self.baseline + 0.05 * advantage
        self.age += 1
        self.n += 1
        novelty = clip(float(novelty), 0.0, 1.0)
        lr_eff = self.lr * novelty
        target = 0.0 if novelty < 0.10 else max(-0.05, min(0.05, 0.10 * norm * novelty))
        old = float(self.mean.get("social_baseline", 0.0))
        denom = max(1, min(self.n, 20))
        self.mean["social_baseline"] = old + (target - old) / denom
        self.delta["social_baseline"] = clip(float(self.mean["social_baseline"]), -0.10, 0.10)
        try:
            self.store.save_meta(self.user_id, {"age": self.age, "n": self.n, "mean": self.mean, "version": 2})
        except Exception:
            pass
        # fast controller owns only social baseline; slow evolution owns lr/autonomy
        self.store.save_delta(self.user_id, {k: round(float(v), 4) for k, v in self.delta.items()})
        try:
            mind.drives.values["social"] = clip(float(mind.drives.values["social"]) + 0.005 * target, 0.0, 1.0)
        except Exception:
            pass
        return {"reward": round(raw, 4), "advantage": round(advantage, 4), "baseline": round(self.baseline, 4), "novelty": round(novelty, 4), "age": self.age, "n": self.n, "lr_eff": round(lr_eff, 4), **{k: round(float(v), 4) for k, v in self.delta.items()}}
