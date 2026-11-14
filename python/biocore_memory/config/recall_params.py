# -*- coding: utf-8 -*-
"""召回参数：一个真源（core_config.json 的 recall.* 段）、运行期读取、夹紧不抛错。

优先级写死为 config > env > 常量缺省：
  - config 是唯一真源，改了立刻生效（不再 import 期冻结，见 ACTUAL-TASKS 30）
  - env 只是一次性覆盖，用它时会打一行 warn，便于事后追查"为什么和配置不一样"
越界一律夹紧到区间并打 warn（参数名 / 给的值 / 生效值），绝不抛错——
一个写错的开关不该让交付失败。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# name -> (default, low, high)
# Provenance and calibration status live in docs/RECALL-PARAM-PROVENANCE.md:
# the core may not carry word lists (tests/test_core_contract.py), so only the
# machine-readable fields stay here.
SPECS = {
    # 切分硬上限生效后（中位 3 句/件），8 件只覆盖约 6k 字，缺省调到 12
    'event_preset': (12, 1, 20),
    'window': (1, 0, 2),
    'relevant_cos': (0.4, 0.0, 1.0),
    'close_chars': (2000, 200, 8000),
    # The ceiling must be >= the default: it used to be 10 < 12, so every
    # configured value was clamped down to 10 and wiring alone changed behaviour.
    'max_turns': (12, 1, 60),
    'max_chars': (900, 200, 4000),
    'pool': (60, 10, 200),
    'seed_k': (40, 1, 200),
    # 沉的开关：0 = 关（缺省），1 = turn_end 写入路径上做事件结算。见 docs/SEVEN-STEPS.md
    'consolidate_on': (0, 0, 1),
    # 画像交付条数（库内不删，交付只取最近更新的 N 条）
    'portrait_top': (8, 1, 50),
    # 「忘」的回忆期筛选。缺省 0（关）：90 天阈值会把整库老数据一次性筛掉，
    # 打开前先跑 dry-run 报数（见 docs/SEVEN-STEPS.md 忘 一节）。
    'forget_filter_on': (0, 0, 1),
    # 续：时间分层交付（≤7 天原文 / 7–90 天概述+原句 / >90 天一句概括），缺省开
    'tiered_delivery_on': (1, 0, 1),
    # 事件交付的相关性门槛：入选事件最高分低于它 → 不注入（答不上就不给）
    'relevant_floor': (0.20, 0.0, 1.0),
    # 干净的事件交付路径（新模块）开关；缺省关，旧路径继续生效
    'event_delivery_clean_on': (0, 0, 1),
    # 交付用原文还是总结：raw（缺省）/ summary / auto（按时间分档）
    'event_text_mode': ('raw', ('raw', 'summary', 'auto')),
    # 超长事件的分段阈值（交付层与沉淀层共用）
    'long_event_chars': (1200, 200, 8000),
    # 长：事件结算后顺手落一条画像（内容由总结层给，记忆侧只落盘），缺省关
    'portrait_on': (0, 0, 1),
    # 压缩挂回：命中的 ts 落在某条官方压缩的时间窗里时，附一次那次的摘要。
    # 缺省开——库里没有压缩记录时它一个字符都不会动（判定不了就不挂）。
    'compaction_on': (1, 0, 1),
    # 一次挂回最多附多少字的压缩摘要（超了按行边界裁，不切句子）
    'compaction_chars': (3000, 200, 12000),
    # —— 已停用，无消费方 ——
    # 常驻注入整条功能已删除（压缩目录本身就是一次性上下文注入，模型要细节用
    # memory_search）。保留这些常量只为老配置文件的解析不炸；新代码不要再读它们。
    'inject_mode': (0, 0, 2),
    'inject_detail': (1, 0, 1),
    'inject_max_chars_summary': (500, 120, 4000),
    # 向量体检+补齐：缺向量的行（entries/units/compactions）在 turn_end 与压缩写入
    # 之后各做一次少量补齐（最多 20 条）。缺省开；关掉只是减少 embedding 调用。
    'vector_backfill_on': (1, 0, 1),
}

# Deliberately NOT consumed yet (2026-11-13 decision, option b): neither value
# has a measured provenance (ACTUAL-TASKS 45), so gating on them would change
# events_shown with an uncalibrated number. They resolve, they are documented
# in docs/RECALL-PARAM-PROVENANCE.md, and no code reads them.
#   relevant_cos -> would become an event relevance floor
#   seed_k       -> would become a cap on seed events
UNUSED = frozenset({'relevant_cos', 'seed_k'})

ENV_NAMES = {
    'event_preset': 'BIOCORE_EVENT_PRESET',
    'window': 'BIOCORE_INJECT_WINDOW',
    'relevant_cos': 'BIOCORE_EVENT_RELEVANT',
    'close_chars': 'BIOCORE_EVENT_CLOSE_CHARS',
    'max_turns': 'BIOCORE_EVENT_MAX_TURNS',
    'max_chars': 'BIOCORE_EVENT_MAX_CHARS',
    'pool': 'BIOCORE_SENTENCE_RERANK_POOL',
    'seed_k': 'BIOCORE_SENTENCE_SEED_K',
    'consolidate_on': 'BIOCORE_CONSOLIDATE_ON',
    'portrait_top': 'BIOCORE_PORTRAIT_TOP',
    'forget_filter_on': 'BIOCORE_FORGET_FILTER',
    'tiered_delivery_on': 'BIOCORE_TIERED_DELIVERY',
    'portrait_on': 'BIOCORE_PORTRAIT_ON',
    'relevant_floor': 'BIOCORE_RELEVANT_FLOOR',
    'event_delivery_clean_on': 'BIOCORE_EVENT_DELIVERY_CLEAN',
    'event_text_mode': 'BIOCORE_EVENT_TEXT_MODE',
    'long_event_chars': 'BIOCORE_LONG_EVENT_CHARS',
    'compaction_on': 'BIOCORE_COMPACTION_ON',
    'compaction_chars': 'BIOCORE_COMPACTION_CHARS',
    'vector_backfill_on': 'BIOCORE_VECTOR_BACKFILL',
    'inject_max_chars_summary': 'BIOCORE_RECALL_INJECT_SUMMARY_CHARS',
    'inject_mode': 'BIOCORE_RECALL_INJECT_MODE',
    'inject_detail': 'BIOCORE_RECALL_INJECT_DETAIL',
}


def _clamp(name, value, default, low, high):
    try:
        number = type(default)(value)
    except Exception:
        logger.warning('[recall] %s=%r 不是数字，回默认 %s', name, value, default)
        return default
    if number < low or number > high:
        bounded = min(high, max(low, number))
        logger.warning('[recall] %s=%s 超出 [%s, %s]，夹紧为 %s',
                       name, number, low, high, bounded)
        return bounded
    return number


_WARNED = set()


def _warn_once(key, fmt, *args):
    """同一进程内同一组合只告警一次，避免运行期每轮刷屏。"""
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(fmt, *args)


def resolve(name, payload=None, env=None):
    """一个参数的生效值：config > env > 默认，全部夹紧。"""
    spec = SPECS[name]
    if len(spec) == 2 and isinstance(spec[1], (tuple, list, set)):
        # 字符串白名单型参数（例如 event_text_mode）：非法值回缺省并 warn，绝不进 _clamp
        default, allowed = spec[0], tuple(spec[1])
        data_s = payload if isinstance(payload, dict) else {}
        recall_s = data_s.get('recall') if isinstance(data_s.get('recall'), dict) else {}
        raw_s = recall_s.get(name)
        if raw_s in (None, ''):
            raw_s = (os.environ if env is None else env).get(ENV_NAMES[name])
        value_s = str(raw_s if raw_s not in (None, '') else default).strip().lower()
        if value_s not in allowed:
            _warn_once((name, value_s), '[recall] %s=%r 不在 %s 里，回退 %s',
                       name, value_s, '/'.join(allowed), allowed[0])
            return allowed[0]
        return value_s
    default, low, high = spec
    data = payload if isinstance(payload, dict) else {}
    recall = data.get('recall') if isinstance(data.get('recall'), dict) else {}
    if name in recall:
        return _clamp(name, recall[name], default, low, high)
    env = os.environ if env is None else env
    raw = env.get(ENV_NAMES[name])
    if raw not in (None, ''):
        value = _clamp(name, raw, default, low, high)
        _warn_once((name, ENV_NAMES[name], raw),
                   '[recall] %s 被环境变量 %s=%s 覆盖（配置里没有该键）',
                   name, ENV_NAMES[name], raw)
        return value
    return default


def all_params(payload=None, env=None):
    return {name: resolve(name, payload, env) for name in SPECS}
