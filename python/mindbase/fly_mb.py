from __future__ import annotations

import os
import time

import numpy as np

# 每个 Kenyon cell 连多少个输入（claws）。0 = 沿用旧的 8% 随机掩码。
# 正整数 = 每个 KC 恰好连 k 个输入（无放回随机选），其余权重为 0。
# 2026-11-16 翻默认：不给值 = 7（Litwin-Kumar 2017 的解剖观测值 6.8 取整）；
# 写 0 回到旧的 8% 掩码（平均 1.84 claws），逐位不变。
# 依据：Litwin-Kumar et al. 2017 (Neuron 93:1153)：表示维度在解剖观测到的连接度处最大；
# Eichler et al. 的 EM 重建里每 KC 平均 6.8 个 claws（2-11）。实测见 docs/MB-SPARSE.md。
SPARSE_K_ENV = "BIOCORE_MB_SPARSE_K"


try:  # L11 三因子：跨仓依赖，取不到就退回旧 Hebbian（关着时等价）
    from biocore_model import mb_three_factor as _tf
    from biocore_model import mb_plasticity_window as _pw3
except Exception:  # pragma: no cover
    _tf = None
    _pw3 = None

try:  # L13 重合检测：跨仓依赖，取不到就退回旧编码（关着时等价）
    from biocore_model import mb_coincidence as _co
except Exception:  # pragma: no cover
    _co = None


def _coincidence_on() -> bool:
    """L13 开关探针：BIOCORE_MB_COINCIDENCE。关 = 不加掩码，旧编码逐位不变。"""
    try:
        return bool(_co is not None and _co.enabled())
    except Exception:
        return False


DEFAULT_SPARSE_K = 7


def _sparse_k() -> int:
    """读 BIOCORE_MB_SPARSE_K；缺省 7，写 0（或负数）回到旧的 8% 掩码。"""
    try:
        return max(0, int(float(os.environ.get(SPARSE_K_ENV) or DEFAULT_SPARSE_K)))
    except Exception:
        return DEFAULT_SPARSE_K


def _mb_apl_on() -> bool:
    """L3 开关探针：BIOCORE_MB_APL。关 = 不调用，旧路径逐字不变。"""
    try:
        from biocore_model import mb_apl as _apl
        return _apl.enabled()
    except Exception:
        return False


def _mb_claw_on() -> bool:
    """L4 开关探针：BIOCORE_MB_CLAW。关 = 不造异质分布（退回 L2 的常数 k，再退回旧掩码）。"""
    try:
        from biocore_model import mb_claw as _claw
        return _claw.enabled()
    except Exception:
        return False


class FlyMushroomBody:
    """Sparse KC code, DAN reward/punishment, MBON approach/avoid, APL feedback."""

    N_KC = 2000
    N_IN = 26   # 16 base + 4 intero + pos/neg + a/stress + dv + v_int
    # 23 -> 24：v 一列按 Cacioppo & Berntson 1999 拆成 pos_act/neg_act 两列；
    # 24 -> 26：再加两列时间维（dv 一阶差分 / v_int 指数滑动积分），依据是
    # affective chronometry（Davidson）：时间进程本身就是情绪的参数。
    # 老检查点的短行仍按零补齐（encode() 的 pad 逻辑不变）。
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
        # 每 KC 连多少个输入，三级退化（2026-11-16 重排）：
        #   L4 开 → 每 KC 的 claws 数取 Poisson(6.8) 截断到 [2, 11]（异质分布）
        #   L4 关、L2 开 → 每 KC 恰好 k 个（常数，k = BIOCORE_MB_SPARSE_K，缺省 7）
        #   两者都关 → 旧的 8% 随机掩码（平均 1.84），逐位不变
        # 早先是 L2 独立改写 w_in、两边同时开时常数 k 把行内非零钉死成 7，L4 的分布在
        # 掩码之后被抹平成常数 → L4 成了死代码（2026-11-16 消融发现）。现在 L4 是
        # "分布从哪来"的来源，L2 只是它关掉时的退化路径。
        _counts = None
        if _mb_claw_on():
            try:
                from biocore_model import mb_claw as _claw
                _counts = _claw.claw_counts(self.N_KC, seed=int(seed))
            except Exception:
                _counts = None
        if _counts is None:
            _k = _sparse_k()
            if _k > 0:
                _counts = [min(int(self.N_IN), int(_k))] * self.N_KC
        if _counts is not None:
            # 每行无放回随机选 claws 个列。用独立的 rng，不动 self.rng ——
            # 于是 readout_idx / w_approach / w_avoid 与稠密那套完全一致，
            # A/B 之间只差"连接模式"这一个变量。
            _rng = np.random.default_rng(int(seed) * 1000003 + 23)
            _w = _rng.normal(0.0, 1.0 / np.sqrt(self.N_IN), (self.N_KC, self.N_IN))
            _order = _rng.random((self.N_KC, self.N_IN)).argsort(axis=1)
            _arity = np.minimum(int(self.N_IN),
                                np.maximum(1, np.asarray(_counts, dtype=int)))
            _w[~(_order < _arity.reshape(-1, 1))] = 0.0
            self.w_in = _w
            self.claw_counts = [int(x) for x in _arity.tolist()]
            # 稠密分支在下面那行用 self.rng 抽同样大小的掩码，本分支不抽，
            # 于是同一 seed 下 readout_idx / w_approach 两套不同（A/B 不纯）。
            # 这里把同样的抽取量"空转"掉，主流保持对齐。
            self.rng.random(self.w_in.shape)
        else:
            self.w_in[self.rng.random(self.w_in.shape) > 0.08] = 0.0
            self.claw_counts = None
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
        self.last_t = None
        self.prev_active = set()    # L13：上一拍被选中的 KC（重合检测用）
        self.last_encode_t = None   # L13：上一拍的墙钟时间（时间窗用）
        self.kc_trace = np.zeros(self.N_KC)   # L11：式(4) 的两条 trace
        self.dan_trace = np.zeros(self.N_KC)
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
        # L5：输入 whitening（默认关；没 fit 过时 apply 是恒等）。由调用方
        # 用 biocore_model.mb_whiten.Whitener().fit(...) 灌进来。
        self._whitener = None

    def set_whitener(self, whitener):
        """L5 注入口：None = 关；未 fit 的 Whitener 也是恒等。"""
        self._whitener = whitener
        return self

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

    def encode(self, features: np.ndarray, dt_s=None) -> np.ndarray:
        f = np.asarray(features, dtype=float).reshape(-1)[: self.N_IN]
        if f.size < self.N_IN:
            f = np.pad(f, (0, self.N_IN - f.size))
        drive = self.w_in @ f + self.rng.normal(0.0, 0.01, self.N_KC)
        if _mb_apl_on():                    # L3：APL 除法归一化挪到选择之前
            from biocore_model import mb_apl as _mb_apl
            drive = np.asarray(_mb_apl.normalize_drive(
                drive, gain=self.apl_gain, ppl1=self.ppl1), dtype=float)
        k = max(1, int(round(self.N_KC * max(0.005, min(0.20, self.sparsity)))))
        idx = np.argpartition(drive, -k)[-k:]
        x = np.zeros(self.N_KC)
        x[idx] = np.tanh(drive[idx])
        total = float(np.sum(np.abs(x)))
        self.apl = float(1.0 / (1.0 + self.apl_gain * (1.0 + 0.5 * self.ppl1) * total))
        if _coincidence_on():
            # L13：KC 只在"时间重合"时保持激活（Demmer & Kloppenburg 2018；
            # 关闭时这一段整块不进，x 逐位不变）。
            # 2026-11-16 修正：早先这里读 time.time()，于是同一段输入在慢机器上
            # 会跨过 2 s 窗口、快机器上不会 —— 同一份数据两次跑出不同的 KC 码
            # （实测：某个测试单跑绿、全量跑红，差的正是这一步）。
            # 现在只认调用方给的每拍间隔 dt_s；不给就当 dt=0（同一拍，算重合），
            # 墙钟不再参与，编码变成纯函数。
            _dt = 0.0 if dt_s is None else max(0.0, float(dt_s))
            _keep = _co.coincident_mask(self.prev_active, {int(i) for i in idx}, _dt)
            if _keep:
                _m = np.zeros(self.N_KC, dtype=bool)
                _m[list(_keep)] = True
                x[~_m] = 0.0
            self.prev_active = {int(i) for i in idx}
            self.last_encode_t = None if dt_s is None else _dt
        x *= self.apl
        self.x = x
        self.last_sparse = float(k) / float(self.N_KC)
        return x

    def step(self, features: np.ndarray, reward: float = 0.0, punishment: float = 0.0,
             emotion=None, systems=None, dt_s=None) -> dict:
        x = self.encode(features, dt_s=dt_s)
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
        if _tf is not None and _tf.enabled():
            # L11 式(4)：dw = lr·dt·(DAN̄·KC − K̄·DAN)，逐 KC 算完再摊到 MBON
            _now = time.time()
            _dt = 1.0 if self.last_t is None else max(0.0, _now - self.last_t)
            self.last_t = _now
            _k = _pw3.decay(_dt) if _pw3 is not None else 0.0
            self.kc_trace = self.kc_trace * _k + x
            _dan = np.zeros(self.N_KC)
            _dan[self.readout_idx] = pam_gain
            self.dan_trace = self.dan_trace * _k + _dan
            _dw = (_tf.learning_rate() * _dt) * (self.dan_trace * x - self.kc_trace * _dan)
            _g = _dw[self.readout_idx] * self.readout_gain
            self.w_approach = np.clip(self.w_approach + np.outer(np.ones(self.N_MBON), _g),
                                      0.0, _tf.weight_max())
            self.w_avoid = np.clip(self.w_avoid - np.outer(np.ones(self.N_MBON), _g),
                                   0.0, _tf.weight_max())
        else:
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
