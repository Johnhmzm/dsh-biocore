from __future__ import annotations

import numpy as np


class FlyMushroomBody:
    """Sparse KC code, DAN reward/punishment, MBON approach/avoid, APL feedback."""

    N_KC = 2000
    N_IN = 23   # 16 base features + 4 intero + v/a/stress (old checkpoints pad zeros)
    N_MBON = 100
    N_DAN = 200
    # 七套情绪系统 == 蘑菇体的七条读出通道（Panksepp 的七个初级系统）。
    # 与 approach/avoid 同构：同一份 KC 稀疏编码，七组权重各自读。
    SYSTEMS = ("SEEKING", "CARE", "PLAY", "LUST", "FEAR", "RAGE", "PANIC")
    N_SYSTEM = 7

    def __init__(self, seed: int = 1, sparsity: float = 0.05, lr: float = 0.01, apl_gain: float = 0.6, subregion_gain=None,
                 pam_gamma_ratio: float = 0.60, ppl1_alpha_ratio: float = 0.50, weight_decay: float = 0.0002,
                 systems_enabled: bool = False):
        self.rng = np.random.default_rng(seed)
        self.sparsity = float(sparsity)
        self.lr = float(lr)
        self.pam_lr = float(lr)
        self.ppl1_lr = float(lr) * 1.2
        self.apl_gain = float(apl_gain)
        self.w_in = self.rng.normal(0.0, 1.0 / np.sqrt(self.N_IN), (self.N_KC, self.N_IN))
        self.w_in[self.rng.random(self.w_in.shape) > 0.08] = 0.0
        self.readout_idx = self.rng.choice(self.N_KC, size=min(200, self.N_KC), replace=False)
        self.w_approach = self.rng.normal(0.0, 0.01, (self.N_MBON, self.readout_idx.size))
        self.w_avoid = self.rng.normal(0.0, 0.01, (self.N_MBON, self.readout_idx.size))
        self.subregion_gain = dict(subregion_gain or {"gamma": 1.0, "alpha": 0.8, "beta": 0.7})
        gains = np.ones(self.readout_idx.size)
        for i, idx in enumerate(self.readout_idx):
            frac = idx / self.N_KC
            gains[i] = self.subregion_gain["gamma"] if frac < 0.40 else (self.subregion_gain["alpha"] if frac < 0.70 else self.subregion_gain["beta"])
        self.readout_gain = gains
        self.x = np.zeros(self.N_KC)
        self.mbon_approach = 0.0
        self.mbon_avoid = 0.0
        self.apl = 0.0
        self.trace = np.zeros(self.N_KC)
        self.trace_decay = 0.90
        self.pam = 0.0
        self.ppl1 = 0.0
        self.reward_expectation = 0.0
        self.punishment_expectation = 0.0
        self.pam_gamma = 0.0
        self.pam_alpha = 0.0
        self.ppl1_alpha = 0.0
        self.ppl1_beta = 0.0
        self.pam_gamma_ratio = float(pam_gamma_ratio)
        self.ppl1_alpha_ratio = float(ppl1_alpha_ratio)
        self.weight_decay = float(weight_decay)
        self.age_steps = 0
        self.plasticity_tau = 2000.0
        self.plasticity = 1.0
        self.rpe = 0.0
        self.ppl1_rpe = 0.0
        self.last_sparse = 0.0
        # 七系统：默认关。关的时候 w_system 不分配、不计算、**不消耗主 rng**
        # （单独的 _sys_rng），所以关掉时所有数值与改动前逐字节一致。
        self.systems_enabled = bool(systems_enabled)
        self.w_system = None
        self._sys_rng = np.random.default_rng(int(seed) * 1000003 + 17)
        self.systems = {}
        self.system_dan = {}

    def set_systems_enabled(self, value) -> bool:
        self.systems_enabled = bool(value)
        return self.systems_enabled

    def _ensure_systems(self) -> bool:
        if not self.systems_enabled:
            return False
        if self.w_system is None:
            self.w_system = self._sys_rng.normal(
                0.0, 0.01, (self.N_SYSTEM, self.N_KC))
        return True

    def _step_systems(self, x, systems: dict) -> dict:
        """七条读出：activation = clip(w_system[k]·KC + bias[k], 0, 1)。

        bias 由调用方按评价直接给（没有模型也能动）；每个系统拿自己的 DAN
        分量做 Hebbian 更新，满足→正向、受挫→负向。
        """
        if not self._ensure_systems():
            return {}
        bias = systems.get("bias") if isinstance(systems.get("bias"), dict) else {}
        dan = systems.get("dan") if isinstance(systems.get("dan"), dict) else {}
        off = systems.get("off") if isinstance(systems.get("off"), (list, tuple, set)) else ()
        off = {str(name) for name in off}
        act = {}
        for i, name in enumerate(self.SYSTEMS):
            b = float(bias.get(name) or 0.0)
            raw = float(np.dot(self.w_system[i], x)) + b
            act[name] = round(max(0.0, min(1.0, raw)), 4)
            if name in off:
                act[name] = 0.0      # 占位但停用：不读出、不学习
                continue
            d = float(dan.get(name) or 0.0)
            # 只有拿到自己那份 DAN 的通道才更新与衰减："各自被自己的奖惩塑形"。
            if d:
                self.w_system[i] += self.lr * self.plasticity * d * x
                self.w_system[i] *= (1.0 - float(self.weight_decay))
        self.systems = act
        self.system_dan = {str(k): round(float(v), 4)
                           for k, v in dan.items()
                           if isinstance(v, (int, float))}
        return act

    def load_input_weights(self, matrix) -> bool:
        """Load an old w_in checkpoint: missing columns are zero-filled (no re-init)."""
        try:
            arr = np.asarray(matrix, dtype=float)
            if arr.ndim != 2 or arr.shape[0] != self.N_KC:
                return False
            out = np.zeros((self.N_KC, self.N_IN), dtype=float)
            cols = min(arr.shape[1], self.N_IN)
            out[:, :cols] = arr[:, :cols]
            self.w_in = out
            return True
        except Exception:
            return False

    def encode(self, features: np.ndarray) -> np.ndarray:
        f = np.asarray(features, dtype=float).reshape(-1)[: self.N_IN]
        if f.size < self.N_IN:
            f = np.pad(f, (0, self.N_IN - f.size))
        drive = self.w_in @ f + self.rng.normal(0.0, 0.01, self.N_KC)
        k = max(1, int(round(self.N_KC * max(0.005, min(0.20, self.sparsity)))))
        idx = np.argpartition(drive, -k)[-k:]
        x = np.zeros(self.N_KC)
        x[idx] = np.tanh(drive[idx])
        total = float(np.sum(np.abs(x)))
        self.apl = float(1.0 / (1.0 + self.apl_gain * (1.0 + 0.5 * self.ppl1) * total))
        x *= self.apl
        self.x = x
        self.last_sparse = float(k) / float(self.N_KC)
        return x

    def step(self, features: np.ndarray, reward: float = 0.0, punishment: float = 0.0,
             emotion=None, systems=None) -> dict:
        x = self.encode(features)
        r = max(0.0, min(1.0, float(reward)))
        p = max(0.0, min(1.0, float(punishment)))
        self.age_steps += 1
        self.plasticity = 1.0 / (1.0 + float(self.age_steps) / float(self.plasticity_tau))
        self.rpe = r - self.reward_expectation
        self.reward_expectation = max(0.0, min(1.0, self.reward_expectation + 0.05 * self.rpe))
        self.ppl1_rpe = p - self.punishment_expectation
        self.punishment_expectation = max(0.0, min(1.0, self.punishment_expectation + 0.05 * self.ppl1_rpe))
        self.pam = max(0.0, self.rpe)
        self.ppl1 = max(0.0, self.ppl1_rpe)
        self.pam_gamma = self.pam_gamma_ratio * self.pam
        self.pam_alpha = (1.0 - self.pam_gamma_ratio) * self.pam
        self.ppl1_alpha = self.ppl1_alpha_ratio * self.ppl1
        self.ppl1_beta = (1.0 - self.ppl1_alpha_ratio) * self.ppl1
        # DAN-gated Hebbian update of MBON readouts
        self.trace = self.trace_decay * self.trace + x
        xr = self.trace[self.readout_idx] * self.readout_gain
        frac = self.readout_idx / self.N_KC
        pam_gain = np.where(frac < 0.40, self.pam_gamma, self.pam_alpha)
        ppl1_gain = np.where((frac >= 0.40) & (frac < 0.70), self.ppl1_alpha, self.ppl1_beta)
        self.w_approach += self.pam_lr * self.plasticity * np.outer(np.ones(self.N_MBON), xr * pam_gain)
        self.w_avoid += self.ppl1_lr * self.plasticity * np.outer(np.ones(self.N_MBON), xr * ppl1_gain)
        decay = max(0.0, min(0.01, float(self.weight_decay)))
        self.w_approach *= (1.0 - decay)
        self.w_avoid *= (1.0 - decay)
        self.mbon_approach = max(0.0, float(np.mean(self.w_approach @ xr))) + 0.05 + 0.20 * r
        self.mbon_avoid = max(0.0, float(np.mean(self.w_avoid @ xr))) + 0.05 + 0.20 * p
        # 情绪先验直接并进 MBON 读数（特征列之外，让"感知到情绪"可测；旧调用
        # emotion=None 时完全不变）。
        if isinstance(emotion, dict):
            try:
                _v = max(-1.0, min(1.0, float(emotion.get("v") or 0.0)))
                _warmth = max(0.0, min(1.0, float(emotion.get("warmth") or 0.0)))
                _tension = max(0.0, min(1.0, float(emotion.get("tension") or 0.0)))
                _stress = max(0.0, min(1.0, float(emotion.get("stress") or 0.0)))
                self.mbon_approach = max(0.0, self.mbon_approach
                                         + 0.50 * max(0.0, _v) + 0.25 * _warmth)
                self.mbon_avoid = max(0.0, self.mbon_avoid
                                      + 0.50 * max(0.0, -_v)
                                      + 0.25 * _tension + 0.15 * _stress)
            except Exception:
                pass
        # 七条读出通道：关着的时候这一段完全不执行（不分配、不计算、不取随机数）
        if systems is not None and self.systems_enabled:
            try:
                self._step_systems(x, systems if isinstance(systems, dict) else {})
            except Exception:
                pass
        out = {
            "kc_sparse": round(self.last_sparse, 4),
            "age_steps": int(self.age_steps),
            "plasticity": round(self.plasticity, 4),
            "apl": round(self.apl, 4),
            "pam": round(self.pam, 4),
            "ppl1": round(self.ppl1, 4),
            "subregion_gain": dict(self.subregion_gain),
            "pam_gamma": round(self.pam_gamma, 4),
            "pam_alpha": round(self.pam_alpha, 4),
            "ppl1_alpha": round(self.ppl1_alpha, 4),
            "ppl1_beta": round(self.ppl1_beta, 4),
            "rpe": round(self.rpe, 4),
            "ppl1_rpe": round(self.ppl1_rpe, 4),
            "reward_expectation": round(self.reward_expectation, 4),
            "punishment_expectation": round(self.punishment_expectation, 4),
            "trace_norm": round(float(np.linalg.norm(self.trace)), 4),
            "mbon_approach": round(self.mbon_approach, 4),
            "mbon_avoid": round(self.mbon_avoid, 4),
        }
        if self.systems_enabled:
            out["systems"] = dict(self.systems)
            out["system_dan"] = dict(self.system_dan)
            out["systems_enabled"] = True
        return out

    def second_order_pair(self, cs2, cs1, reward: float = 0.8, repeats: int = 2) -> dict:
        """Present CS2 then CS1 with reward on CS1; repeat to strengthen second-order value."""
        out = None
        for _ in range(max(1, int(repeats))):
            self.step(cs2, reward=0.0)
            out = self.step(cs1, reward=reward)
        return out

    def snapshot(self) -> dict:
        out = {
            "kc": self.N_KC,
            "mbon": self.N_MBON,
            "dan": self.N_DAN,
            "sparsity": round(self.sparsity, 4),
            "lr": round(self.lr, 4),
            "pam_lr": round(self.pam_lr, 4),
            "ppl1_lr": round(self.ppl1_lr, 4),
            "apl_gain": round(self.apl_gain, 4),
            "pam": round(self.pam, 4),
            "ppl1": round(self.ppl1, 4),
            "subregion_gain": dict(self.subregion_gain),
            "pam_gamma": round(self.pam_gamma, 4),
            "pam_alpha": round(self.pam_alpha, 4),
            "ppl1_alpha": round(self.ppl1_alpha, 4),
            "ppl1_beta": round(self.ppl1_beta, 4),
            "pam_gamma_ratio": round(self.pam_gamma_ratio, 4),
            "ppl1_alpha_ratio": round(self.ppl1_alpha_ratio, 4),
            "weight_decay": round(self.weight_decay, 5),
            "age_steps": int(self.age_steps),
            "plasticity": round(self.plasticity, 4),
            "trace_norm": round(float(np.linalg.norm(self.trace)), 4),
            "ppl1_rpe": round(self.ppl1_rpe, 4),
            "punishment_expectation": round(self.punishment_expectation, 4),
            "kc_sparse": round(self.last_sparse, 4),
            "mbon_approach": round(self.mbon_approach, 4),
            "mbon_avoid": round(self.mbon_avoid, 4),
        }
        if self.systems_enabled:
            out["systems_enabled"] = True
            out["systems"] = dict(self.systems)
            out["system_weight_norm"] = (
                round(float(np.linalg.norm(self.w_system)), 6)
                if self.w_system is not None else 0.0)
        return out
