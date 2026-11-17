from __future__ import annotations

from typing import Dict
import random
import numpy as np
from .fly_mb import FlyMushroomBody

# Cacioppo & Berntson 1999 (Evaluative Space Model)：正负效价不是一根轴上的两个方向，
# 而是两个各自独立的激活系统（bivariate），可以同时为 0，也可以同时为高。
# 同一个模型还说：零输入时正向系统的活动高于中性基线（positivity offset）。
# 所以 v 不再以"带符号的一列"进蘑菇体，而是拆成两列正/负激活。
# 依据：Cacioppo, Gardner & Berntson 1999, "The affect system has parallel and
# integrative processing components", J Pers Soc Psychol 76:839。
POSITIVITY_OFFSET = 0.0   # 先取 0；A/B 定值后再改这一处（不要在各调用点加常数）


def positive_activation(v) -> float:
    """正向激活 = max(v, 0) + offset。"""
    try:
        value = float(v)
    except Exception:
        value = 0.0
    return max(value, 0.0) + float(POSITIVITY_OFFSET)


def negative_activation(v) -> float:
    """负向激活 = max(-v, 0)。"""
    try:
        value = float(v)
    except Exception:
        value = 0.0
    return max(-value, 0.0)


class Circuit:
    """Coarse mushroom-body populations and sparse activity (FlyWire-style scale)."""

    FLYWIRE_TOTAL = 139255
    FLYWIRE = {"KC": 2000, "DAN": 200, "MBON": 100, "APL": 1}
    MB_SUBREGIONS = {"gamma": 0.40, "alpha": 0.30, "beta": 0.30}
    SUBREGION_RATES = {"gamma": 1.00, "alpha": 0.80, "beta": 0.70}
    SUBREGION_MEMORY = {"gamma": "short", "alpha": "long", "beta": "long"}
    SUBREGION_TAU_DAYS = {"gamma": 1.0, "alpha": 30.0, "beta": 20.0}
    # 情绪列（4 内感受 + v/a/stress）在 KC 前的放大：让感知可测，不改旧 8 列
    EMOTION_FEATURE_GAIN = 6.0

    POPULATIONS = {
        "sensory": 3000,
        "KC": 2000,
        "DAN": 200,
        "MBON": 100,
        "APL": 1,
    }

    def __init__(self, systems_enabled=None):
        # 七套情绪系统：None = 看环境变量（BIOCORE_EMOTION_SYSTEMS，默认开）
        if systems_enabled is None:
            import os as _os
            flag = str(_os.environ.get("BIOCORE_EMOTION_SYSTEMS") or "1").strip().lower()
            systems_enabled = flag not in ("0", "false", "off", "no", "")
        self.systems_enabled = bool(systems_enabled)
        self.systems = {}
        self.system_dan = {}
        self.activity = {k: 0.0 for k in self.POPULATIONS}
        self.dan_reward = 0.0
        self.dan_punishment = 0.0
        self.mbon_approach = 0.0
        self.mbon_avoid = 0.0
        self.lesions = set()
        self.lesion_timers = {}
        self.lesion_strength = {}
        self.lesion_rate = {}
        self.recovery_speed = 1.0
        self.recovery_lr = 0.01
        self.connection_gain = {"KC": 1.0, "DAN": 1.0, "MBON": 1.0}
        self.connection_lr = 0.005
        self.sparsity = 0.05
        self.fly_mb = None
        self.pending_features = None
        self._v_trace = None    # L1 时间维：dv / v_int 的跨拍状态（懒建）
        self.second_order_events = 0
        self.second_order_gain = 0.0
        self.chronic_scar = 0.0
        self.scar_floor = 0.05

    def update(self, salience: float, sparse: float | None = None, reward: float = 0.0, punishment: float = 0.0,
               intero=None, affect=None, systems=None, dt_s=None) -> Dict[str, float]:
        for region, left in list(self.lesion_timers.items()):
            self.lesion_strength[region] = max(0.0, self.lesion_strength.get(region, 0.0) - self.lesion_rate.get(region, 0.0))
            if left <= 1 or self.lesion_strength[region] <= 1e-9:
                self.recover(region)
            else:
                self.lesion_timers[region] = left - 1
        sparse = self.sparsity if sparse is None else float(sparse)
        s = max(0.0, min(1.0, float(salience)))
        self.dan_reward = max(0.0, min(1.0, float(reward)))
        self.dan_punishment = max(0.0, min(1.0, float(punishment)))
        features = np.array([s, self.dan_reward, self.dan_punishment, 1.0,
                             s * (1.0 - self.dan_reward), s * (1.0 - self.dan_punishment),
                             self.chronic_scar, 0.5], dtype=float)
        # 情绪也进蘑菇体：L1 内感受四量 + L2 正/负激活 + a/stress（只增列，老 8 列不动；
        # FlyMushroomBody.N_IN=24，不足补 0）。这一层是"感知情绪"，不是决策。
        # v 按评价空间模型拆成 pos/neg 两列（见文件头 POSITIVITY_OFFSET）。
        try:
            _intero = intero if isinstance(intero, dict) else {}
            _affect = affect if isinstance(affect, dict) else {}
            def _clip01(v):
                try:
                    return max(0.0, min(1.0, float(v)))
                except Exception:
                    return 0.0
            def _clipv(v):
                try:
                    return max(-1.0, min(1.0, float(v)))
                except Exception:
                    return 0.0
            _g = float(self.EMOTION_FEATURE_GAIN)
            _coords = None
            try:                                    # L1：坐标化（默认关）
                from biocore_model import mb_coord as _mb_coord
                if _mb_coord.enabled():
                    _coords = _mb_coord.coord_vector(_intero, _affect)
            except Exception:
                _coords = None
            # L1 的时间维：dv（一阶差分）与 v_int（指数滑动积分）。
            # 依据 affective chronometry：情绪的起始/上升/恢复是参数，单点强度不够。
            _cols = None
            try:
                from biocore_model import mb_coord as _mb_temporal
                if self._v_trace is None:
                    self._v_trace = _mb_temporal.TemporalTrace()
                _cols = _mb_temporal.temporal_columns(
                    self._v_trace, _clipv(_affect.get("v")), dt_s)
            except Exception:
                _cols = None
            if _coords is not None:
                features = np.concatenate([features, np.array(_coords, dtype=float) * _g])
            else:
                features = np.concatenate([features, np.array([
                    _clip01(_intero.get("tension")),
                    _clip01(_intero.get("warmth")),
                    _clip01(_intero.get("heaviness")),
                    _clip01(_intero.get("clarity")),
                    positive_activation(_clipv(_affect.get("v"))),
                    negative_activation(_clipv(_affect.get("v"))),
                    _clip01(_affect.get("a")),
                    _clip01(_affect.get("stress")),
                ], dtype=float) * _g])
            if _cols:
                # 与情绪块同一份放大系数：不这么写，两列时间维就比别的情绪列小 6 倍，
                # 等于在 w·f 里天生没有话语权（这是量纲一致，不是调参）。
                features = np.concatenate([features, np.array(_cols, dtype=float) * _g])
        except Exception:
            pass
        if self.fly_mb is None:
            self.fly_mb = FlyMushroomBody(seed=1, sparsity=self.sparsity,
                                          systems_enabled=self.systems_enabled)
        if self.fly_mb is None:
            self.fly_mb = FlyMushroomBody(seed=1, sparsity=self.sparsity,
                                          systems_enabled=self.systems_enabled)
        dan_lesion = float(self.lesion_strength.get("DAN", 0.0))
        dan_factor = max(0.0, 1.0 - dan_lesion)
        if (self.dan_reward > 0.0 or self.dan_punishment > 0.0) and self.pending_features is not None:
            before = float(self.fly_mb.mbon_approach)
            self.fly_mb.second_order_pair(self.pending_features, features,
                                          reward=min(1.0, max(self.dan_reward, self.dan_punishment) * dan_factor * 1.5), repeats=3)
            self.second_order_gain = round(float(self.fly_mb.mbon_approach) - before, 4)
            self.second_order_events += 1
            self.pending_features = None
        elif self.dan_reward <= 0.0 and self.dan_punishment <= 0.0:
            self.pending_features = features.copy()
        _emotion_readout = None
        if isinstance(intero, dict) or isinstance(affect, dict):
            _emotion_readout = {
                "v": float((affect or {}).get("v") or 0.0),
                "warmth": float((intero or {}).get("warmth") or 0.0),
                "tension": float((intero or {}).get("tension") or 0.0),
                "stress": float((affect or {}).get("stress") or 0.0),
            }
        mb = self.fly_mb.step(features, reward=self.dan_reward * dan_factor,
                              punishment=self.dan_punishment * dan_factor,
                              emotion=_emotion_readout, systems=systems,
                              dt_s=dt_s)
        # 七系统读数单独存，不塞进 activity：activity 是按 POPULATIONS 加权的，
        # 多出七个键会把脑区活动率算错。
        if self.fly_mb is not None and self.fly_mb.systems_enabled:
            self.systems = dict(getattr(self.fly_mb, "systems", {}) or {})
            self.system_dan = dict(getattr(self.fly_mb, "system_dan", {}) or {})
        self.sparsity = float(self.fly_mb.sparsity)
        self.activity["sensory"] = s
        self.activity["KC"] = round(float(mb["kc_sparse"]), 4)
        self.activity["DAN"] = max(self.dan_reward, self.dan_punishment)
        self.mbon_approach = max(0.0, float(mb["mbon_approach"]))
        self.mbon_avoid = max(0.0, float(mb["mbon_avoid"]))
        self.activity["MBON"] = min(1.0, max(self.mbon_approach, self.mbon_avoid))
        self.activity["APL"] = float(mb["apl"])
        out = dict(self.activity)
        if self.systems:
            out["systems"] = dict(self.systems)
        return out

    def memory_retention(self, subregion: str, days: float) -> float:
        import math
        tau = max(1e-6, self.SUBREGION_TAU_DAYS.get(subregion, 1.0))
        return round(math.exp(-max(0.0, float(days)) / tau), 4)

    def chronic_stress(self, amount: float = 0.01) -> None:
        sev = min(0.80, self.lesion_strength.get("DAN", 0.0) + max(0.0, float(amount)))
        self.lesions.add("DAN")
        self.lesion_strength["DAN"] = sev
        self.chronic_scar = min(0.50, self.chronic_scar + 0.001 * max(0.0, float(amount)) * 20.0)
        for k in self.connection_gain:
            self.connection_gain[k] = max(0.50, self.connection_gain[k] - 0.20 * max(0.0, float(amount)))

    def sleep_recovery(self, duration: float) -> Dict[str, float]:
        factor = min(1.0, max(0.0, float(duration)) / (8.0 * 3600.0))
        for region in list(self.lesion_strength):
            self.lesion_strength[region] = max(0.0, self.lesion_strength[region] - 0.5 * factor)
            if self.lesion_strength[region] <= 1e-9:
                self.recover(region)
        for k in self.connection_gain:
            self.connection_gain[k] += (1.0 - self.connection_gain[k]) * 0.20 * factor
        self.sparsity += (0.05 - self.sparsity) * 0.20 * factor
        if self.fly_mb is not None:
            self.fly_mb.sparsity = self.sparsity
        self.chronic_scar *= max(0.0, 1.0 - 0.02 * factor)
        return {"factor": round(factor, 3), "lesions": sorted(self.lesions), "scar": round(self.chronic_scar, 4)}

    def learn_connection(self, rpe: float) -> None:
        for k in self.connection_gain:
            self.connection_gain[k] = max(0.50, min(1.50, self.connection_gain[k] + self.connection_lr * float(rpe)))

    def learn_recovery(self, rpe: float) -> None:
        self.recovery_speed = max(0.2, min(3.0, self.recovery_speed + self.recovery_lr * float(rpe)))

    def lesion(self, region: str, ticks: int | None = None, severity: float = 1.0) -> None:
        region = str(region)
        self.lesions.add(region)
        self.lesion_strength[region] = max(0.0, min(1.0, float(severity)))
        if ticks is not None:
            self.lesion_timers[region] = int(ticks)
            self.lesion_rate[region] = self.lesion_strength[region] / max(1, int(ticks)) * self.recovery_speed

    def recover(self, region: str) -> None:
        region = str(region)
        self.lesions.discard(region)
        self.lesion_timers.pop(region, None)
        self.lesion_strength.pop(region, None)
        self.lesion_rate.pop(region, None)

    def learning_gain(self) -> float:
        g = sum(self.MB_SUBREGIONS[k] * self.SUBREGION_RATES[k] for k in self.MB_SUBREGIONS)
        fly = getattr(self, "fly_mb", None)
        if fly is not None:
            default_sum = sum({"gamma": 1.0, "alpha": 0.8, "beta": 0.7}.values())
            g *= sum(float(v) for v in fly.subregion_gain.values()) / default_sum
        g *= sum(self.connection_gain.values()) / len(self.connection_gain)
        g *= 1.0 - self.chronic_scar
        dan = self.lesion_strength.get("DAN", 0.0)
        g *= 1.0 - 0.5 * dan
        return g

    def valence(self) -> float:
        # MBON output is approach; DAN is modulation/punishment
        mbon = self.lesion_strength.get("MBON", 0.0)
        dan = self.lesion_strength.get("DAN", 0.0)
        approach = float(self.mbon_approach) * (1.0 - mbon) * self.connection_gain["MBON"]
        avoid = float(self.mbon_avoid) * (1.0 - mbon) * self.connection_gain["MBON"]
        reward = self.dan_reward * (1.0 - dan) * self.connection_gain["DAN"]
        punish = self.dan_punishment * (1.0 - dan) * self.connection_gain["DAN"]
        return approach + 0.5 * reward - avoid - 0.5 * punish

    def behavior_bias(self) -> str:
        if self.chronic_scar >= 0.25:
            return "avoid"
        v = self.valence()
        if v > 0.15: return "approach"
        if v < -0.10: return "avoid"
        return "neutral"

    def spontaneous_signal(self, social_need: float = 0.0, unfinished: float = 0.0, attachment: float = 0.0) -> float:
        """Low-level noise + social deficit; used to trigger voluntary speech, not timed proactive."""
        kc = float(self.activity.get("KC", 0.0))
        apl = float(self.activity.get("APL", 0.0))
        noise = abs(random.gauss(0.0, 1.0))
        drive = (0.08 + 0.55 * min(1.0, noise / 2.5) + 0.15 * max(0.0, 1.0 - apl) + 0.10 * min(1.0, kc * 8.0)
                 + 0.30 * max(0.0, min(1.0, float(social_need)))
                 + 0.20 * max(0.0, min(1.0, float(unfinished)))
                 + 0.15 * max(0.0, min(1.0, float(attachment))))
        self.last_spontaneous = round(float(min(1.0, drive)), 4)
        return self.last_spontaneous

    def metabolic_cost(self) -> float:
        # population activity weighted by size, normalized
        units = sum(self.activity[k] * self.POPULATIONS[k] for k in self.POPULATIONS)
        return min(1.0, units / 5000.0)

    def enforce_capacity(self, limit: float = 0.90) -> Dict[str, object]:
        rep = self.capacity_report()
        if rep["utilization"] <= limit:
            if rep["utilization"] < 0.35:
                self.sparsity = max(0.02, self.sparsity * 0.99)
                if self.fly_mb is not None:
                    self.fly_mb.sparsity = self.sparsity
            return {"overloaded": False, "scale": 1.0}
        scale = limit / max(1e-9, rep["utilization"])
        self.sparsity = min(0.20, self.sparsity * 1.20)
        if self.fly_mb is not None:
            self.fly_mb.sparsity = self.sparsity
        for k in self.activity:
            self.activity[k] *= scale
        self.overloaded = True
        return {"overloaded": True, "scale": round(scale, 4)}

    def action_activity(self, salience: float) -> float:
        util = float(self.capacity_report()["utilization"])
        return round(max(0.0, float(salience)) * (1.0 + util), 4)

    def write_gain(self) -> float:
        util = float(self.capacity_report()["utilization"])
        return round(max(0.40, 1.0 - 0.60 * max(0.0, util - 0.50)), 4)

    def capacity_report(self) -> Dict[str, object]:
        total = sum(self.POPULATIONS.values())
        active = sum(self.activity[k] * self.POPULATIONS[k] for k in self.POPULATIONS)
        return {
            "total_capacity": total,
            "active_units": round(active, 1),
            "utilization": round(active / max(1, total), 4),
            "limit": 0.90,
            "within_limit": (active / max(1, total)) <= 0.90,
            "sparsity": round(self.sparsity, 4),
        }

    def structure_report(self) -> Dict[str, object]:
        mb = sum(self.POPULATIONS[k] for k in ("KC", "DAN", "MBON", "APL"))
        return {
            "total": self.FLYWIRE_TOTAL,
            "mb_neurons": mb,
            "mb_fraction": round(mb / self.FLYWIRE_TOTAL, 5),
            "reference": dict(self.FLYWIRE),
            "subregions": {k: round(v * self.POPULATIONS["KC"], 1) for k, v in self.MB_SUBREGIONS.items()},
            "memory_types": dict(self.SUBREGION_MEMORY),
        }

    def snapshot(self) -> Dict[str, object]:
        active = {k: round(self.activity[k] * self.POPULATIONS[k], 1) for k in self.POPULATIONS}
        return {"populations": dict(self.POPULATIONS), "activity": {k: round(v, 3) for k, v in self.activity.items()}, "active_units": active, "dan_reward": round(self.dan_reward, 3), "dan_punishment": round(self.dan_punishment, 3), "mbon_approach": round(self.mbon_approach, 3), "mbon_avoid": round(self.mbon_avoid, 3), "lesions": sorted(self.lesions), "recovery_speed": round(self.recovery_speed, 3), "connection_gain": {k: round(v, 3) for k, v in self.connection_gain.items()}}
