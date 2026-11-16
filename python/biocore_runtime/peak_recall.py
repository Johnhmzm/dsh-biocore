# -*- coding: utf-8 -*-
"""峰值召回：把"这条记忆当时有多激动"折进召回分数（默认关，BIOCORE_PEAK_RECALL）。

依据：杏仁核-海马那条线（McGaugh 2004）——唤起的强度决定记忆被优先取出的程度，
而不是"好/坏"。所以这里只用唤醒幅值 a（0..1），不看效价正负，也不改任何算法结构：

    final = base * (1 + weight * a)      weight 默认 0.12（BIOCORE_PEAK_WEIGHT）

为什么默认从 0.35 降到 0.12：见 docs/TUNE-LEDGER.md 第 4 条与 docs/TUNE-LIT.md——
0.35 会把 top5 里高唤醒条目从 13.6% 抬到 71.4%（top1 50.0%），唤醒成了第一解释项；
0.12 落在 30.7%（区间 25%–35% 内），top1 17.9% 仍贴着全库 18.7% 的基率，名次只动 1.41 位。

为什么乘而不加：base 是余弦/BM25 那一套分数，量纲各家不同；乘一个 (1+..) 的因子
不会改变排序的相对结构，只在同分区间把高唤起的往前挪。

关着时：`enabled()` 为假，pipeline 里那一段 try 不执行，scored 逐项不变。
"""
from __future__ import annotations

import json
import os

ENV = 'BIOCORE_PEAK_RECALL'
ENV_WEIGHT = 'BIOCORE_PEAK_WEIGHT'
DEFAULT_WEIGHT = 0.12
YES = ('1', 'true', 'on', 'yes')


def enabled(value=None) -> bool:
    if isinstance(value, bool):
        return value
    raw = os.environ.get(ENV) if value is None else value
    return str(raw or '').strip().lower() in YES


def weight(value=None) -> float:
    raw = os.environ.get(ENV_WEIGHT) if value is None else value
    try:
        got = float(raw if raw not in (None, '') else DEFAULT_WEIGHT)
    except Exception:
        return DEFAULT_WEIGHT
    return max(0.0, min(2.0, got))


def arousal_of(row):
    """一条记忆的唤醒幅值 0..1：raw.affect.a → raw.affect.arousal → 0。"""
    if not isinstance(row, dict):
        return 0.0
    raw = row.get('raw')
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if not isinstance(raw, dict):
        raw = row
    affect = raw.get('affect') if isinstance(raw.get('affect'), dict) else {}
    for key in ('a', 'arousal', 'peak'):
        value = affect.get(key, raw.get(key))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, min(1.0, float(value)))
    return 0.0


def boost(value, row, w=None):
    """单个分数乘上 (1 + w·a)。"""
    try:
        base = float(value)
    except Exception:
        return value
    gain = 1.0 + float(weight(w)) * arousal_of(row)
    return base * gain


def boost_rows(scored, w=None):
    """[(score, row)] -> [(score*(1+w·a), row)]；结构原样，只改数字。"""
    rows = []
    for value, row in (scored or []):
        rows.append((boost(value, row, w), row))
    return rows
