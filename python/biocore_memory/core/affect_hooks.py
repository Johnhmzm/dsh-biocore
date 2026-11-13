# -*- coding: utf-8 -*-
"""情绪接口占位：签名先定，实现等通道。

SEVEN-STEPS 里"有效年龄 = 年龄 / (1 + κ×情绪峰值)"，以及"结果回传 / 节奏"两个
驱动，全都依赖 summarizer 与评价通道（峰值、κ、评价八维）。通道现在 401，
所以这里只留接口、不写规则：

  - effective_age(age, peak=0.0, kappa=0.0)：kappa 默认 0.0 时等价于"只看时间"，
    通道可用后把峰值与 κ 传进来即可，调用方不用改签名。
  - event_outcome_delta(event) / rhythm_delta(session)：恒返回 0.0，
    待通道与规则确定后实现（现在不许猜权重）。

不新增任何未使用的字段：trace 里的 affect 保持原样，这里只做函数出口。
"""
from __future__ import annotations


def effective_age(age, peak=0.0, kappa=0.0):
    """有效年龄：age / (1 + kappa * peak)；缺省 kappa=0.0 时就是 age。

    peak 与 kappa 由评价通道产出（情绪峰值、标定系数）。非数值、负数一律
    按 0 处理；分母非正时回退成原始 age，绝不返回负数或抛错。
    """
    try:
        base = float(age)
    except (TypeError, ValueError):
        return 0.0
    try:
        peak_value = max(0.0, float(peak))
    except (TypeError, ValueError):
        peak_value = 0.0
    try:
        kappa_value = max(0.0, float(kappa))
    except (TypeError, ValueError):
        kappa_value = 0.0
    denominator = 1.0 + kappa_value * peak_value
    if denominator <= 0.0:
        return base
    return base / denominator


def event_intensity(event):
    """事件强度 0..1，用于事件打分；缺省 0 = 不参与排序。由情绪层提供。"""
    return 0.0


def peak_of(events):
    """情绪峰值 0..1，用于有效年龄折算；缺省 0 = 只按时间。由情绪层提供。"""
    return 0.0


def mood_bias(state):
    """心情偏置 -1..1，用于召回偏置；缺省 0 = 不偏。由情绪层提供。"""
    return 0.0


def on_event(event):
    """事件回传给情绪层；缺省空实现（不参与计算）。由情绪层实现。"""
    return None


def event_outcome_delta(event):
    """事件结果对情绪的影响：暂返回 0.0（待通道/规则确定后实现）。"""
    return 0.0


def rhythm_delta(session):
    """节奏对情绪的影响：暂返回 0.0（待通道/规则确定后实现）。"""
    return 0.0
