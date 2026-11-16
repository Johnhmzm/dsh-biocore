# -*- coding: utf-8 -*-
"""B1: one evidence accumulator + three drift rates.

依据：习惯与目标在强化学习 + 序列采样下可以合成同一个累积器
（Comp Brain Behav 2024, "Habits and goals as reinforcement learning with
sequence sampling"）：不是三套判决，而是同一个证据槽被三条不同时间常数的
漂移率灌进去。

本模块只做合成，不搬旧逻辑：

  rate_habit()  快（一拍一分钟）：挫折计数、静默拍数、不应期
  rate_goal()   中：直接委托 biocore_model.autonomy_model.drift_value(parts)
  rate_need()   慢（小时级）：五个需要的亏空

三条旧路径（见 DEPRECATED_PATHS）在 BIOCORE_ONE_ACCUM=1 时**降级为输入**，
只提供漂移率，不再各自判决；=0（缺省）时调用方原样用旧判决，本模块一次都
不被调用，行为逐字不变。

默认关：BIOCORE_ONE_ACCUM 缺省 "0"。翻默认要有前后对照（docs/B1-LEDGER.md）。
"""
from __future__ import annotations

import math
import os

FLAG = "BIOCORE_ONE_ACCUM"

# 与 autonomy_model.step 同一套约定：V 是累积证据，>=theta 就烧掉并清零。
LEAK = 0.15
THETA = 0.55
SIGMA = 0.0            # 缺省无噪声：可复现优先，噪声由调用方按需给
RATE_MAX = 1.0

# 三路漂移率的权重：合成一个数之前各自先被自己的时间常数压过。
W_HABIT = 0.45
W_GOAL = 0.40
W_NEED = 0.35

# 三条旧判决路径（只读清单；BIOCORE_ONE_ACCUM=1 时它们只喂漂移率）。
DEPRECATED_PATHS = (
    {"id": "habit", "what": "L5 hazard -> p_speak -> decision.action",
     "where": "biocore_runtime/pipeline.py:_body_loop_impl (hazard / _p_speak)",
     "state": "DEPRECATED once ONE_ACCUM is on: feeds rate_habit only"},
    {"id": "goal", "what": "autonomy V accumulation -> fire -> action",
     "where": "biocore_model/autonomy_model.py:drift_value/step, autonomy_tick.tick_once",
     "state": "DEPRECATED once ONE_ACCUM is on: feeds rate_goal only"},
    {"id": "need", "what": "proactive_tick / standby should_send gate",
     "where": "biocore_runtime/pipeline.py:proactive_tick, biocore_runtime/standby.py:tick",
     "state": "DEPRECATED once ONE_ACCUM is on: feeds rate_need only"},
)


def _clamp(value, lo=0.0, hi=RATE_MAX):
    try:
        return max(lo, min(hi, float(value)))
    except Exception:
        return lo


def _num(value, default=0.0):
    try:
        number = float(value)
    except Exception:
        return float(default)
    if math.isnan(number) or math.isinf(number):
        return float(default)
    return number


def enabled(env=None):
    """BIOCORE_ONE_ACCUM（缺省关）：每次现读，测试才能钉住"关掉=旧行为"。"""
    source = os.environ if env is None else env
    try:
        raw = str(source.get(FLAG) or "0").strip().lower()
    except Exception:
        raw = "0"
    return raw in ("1", "true", "on", "yes")


def rate_habit(silent_beats=0, frustration_n=0, refractory_beats=0, spont_tau_h=6.0):
    """快通路：静默越久越想开口，刚说过话要冷却，挫折是可变强化。

    三项都先各自饱和再相乘：任何一项接近 0 都能把这一路压住（刚说完话不
    立刻再说），三项都高才给满漂移。返回 [0, 1]。
    """
    silent = max(0.0, _num(silent_beats))
    tau_beats = max(0.2, _num(spont_tau_h, 6.0) * 60.0)
    silence_term = 1.0 - math.exp(-silent / tau_beats)
    refractory = max(0.0, _num(refractory_beats))
    refr_term = 1.0 - math.exp(-refractory / 4.0)
    frust = max(0.0, _num(frustration_n))
    frust_term = 1.0 - math.exp(-frust / 1.5)
    return round(_clamp(silence_term * (0.35 + 0.65 * refr_term) * (0.5 + 0.5 * frust_term)), 4)


def rate_goal(parts, weights=None, traits=None):
    """中通路：目标/关系驱动的证据，直接复用 autonomy_model.drift_value。

    这里不重写权重：同一份 parts 必须给出同一个数，否则两条路径会在
    切换开关的那一天给出不同的动作，前后就无法对照。
    """
    try:
        from biocore_model import autonomy_model as _am
        return round(_clamp(_am.drift_value(parts or {}, weights=weights, traits=traits)), 4)
    except Exception:
        return 0.0


def rate_need(social=0.5, safety=0.5, esteem=0.5, control=0.5, meaning=0.5):
    """慢通路：五个需要的亏空均值（需要是 0..1 的"满足度"，越低越缺）。"""
    deficits = [_clamp(1.0 - _clamp(v)) for v in (social, safety, esteem, control, meaning)]
    return round(_clamp(sum(deficits) / len(deficits)), 4)


def drift_rates(habit=0.0, goal=0.0, need=0.0, weights=None):
    """三路合成一个漂移率（不是三票）：权重只决定谁更响，不决定谁赢。"""
    w = {"habit": W_HABIT, "goal": W_GOAL, "need": W_NEED}
    w.update(weights or {})
    parts = {
        "habit": round(_clamp(_num(habit)) * _num(w["habit"], W_HABIT), 4),
        "goal": round(_clamp(_num(goal)) * _num(w["goal"], W_GOAL), 4),
        "need": round(_clamp(_num(need)) * _num(w["need"], W_NEED), 4),
    }
    total = round(sum(parts.values()), 4)
    return {"rates": parts, "drift": total}


def step(V, drift, leak=LEAK, theta=THETA, sigma=SIGMA, xi=None, rng=None):
    """一拍：V = V*(1-leak) + drift + xi；过线就开口并清零。

    与 autonomy_model.step 的区别只有一个：这里同时接受三路漂移率的明细，
    便于事后回答"这一拍是谁把它顶上去的"。
    """
    import random

    rates = drift.get("rates") if isinstance(drift, dict) else None
    value_drift = _num(drift.get("drift") if isinstance(drift, dict) else drift, 0.0)
    source = rng or random
    if xi is not None:
        noise = _num(xi)
    elif _num(sigma) > 0.0:
        noise = float(source.gauss(0.0, _num(sigma)))
    else:
        noise = 0.0
    leak_v = _clamp(leak, 0.0, 0.5)
    theta_v = max(1e-6, _num(theta, THETA))
    raw = _num(V) * (1.0 - leak_v) + value_drift + noise
    value = max(0.0, raw)
    fire = bool(value >= theta_v)
    return {
        "V": 0.0 if fire else round(value, 4),
        "V_raw": round(raw, 4),
        "fire": fire,
        "drift": round(value_drift, 4),
        "rates": dict(rates) if isinstance(rates, dict) else {},
        "xi": round(noise, 4),
        "theta": round(theta_v, 4),
        "to_threshold": round(max(0.0, theta_v - value), 4),
    }


def decide(legacy, **kwargs):
    """开关是这把锁：关着时把旧判决**原样**交回去（同一个对象，不添键）。

    kwargs 只在开着时用来构造累积器（V/habit/goal/need/weights/leak/theta…）。
    """
    if not enabled(kwargs.pop("env", None)):
        return legacy
    rates = drift_rates(
        habit=kwargs.get("habit", 0.0),
        goal=kwargs.get("goal", 0.0),
        need=kwargs.get("need", 0.0),
        weights=kwargs.get("weights"),
    )
    out = step(kwargs.get("V", 0.0), rates,
               leak=kwargs.get("leak", LEAK), theta=kwargs.get("theta", THETA),
               sigma=kwargs.get("sigma", SIGMA), xi=kwargs.get("xi"),
               rng=kwargs.get("rng"))
    action = "speak" if out["fire"] else "silence"
    out["action"] = action
    out["legacy_action"] = (legacy or {}).get("action") if isinstance(legacy, dict) else None
    out["mode"] = "one_accum"
    return out
