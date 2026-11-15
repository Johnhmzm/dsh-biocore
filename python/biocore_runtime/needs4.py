# -*- coding: utf-8 -*-
"""四需要 + 睡眠债 + 事件→需要的落盘层（needs_esteem / needs_sleep / needs_events）。

现状缺口（带文件:行）：
  - 四需要在 L3 每拍现算就丢（biocore_runtime/pipeline.py 的 L3 只写 layers），
    会话之间记不住“孤独了多久”；
  - 睡眠债在 L1 的 source 里是写死的 0.0（biocore_runtime/pipeline.py:7424 附近
    的 "sleep_debt": 0.0），intero_core.target 里那两项（tension += 0.20·debt、
    clarity 用 1−debt）从来没被喂过。

文献结论（详见 docs/AFFECT-LIT.md §11）：
  - Williams 的 temporal need-threat model 给的是**四个需要的方向**（belonging /
    self-esteem / control / meaningful existence），**不含任何数值**；
  - Borbély 1982 的两过程模型给的是 Process S（睡眠债随清醒累积、随睡眠偿还）的
    **方向**，同样不给每小时斜率；
  - Leary 1995 的 sociometer 给的是“自尊跟踪被接纳程度”的**方向**。
所以本模块**不新增任何阈值**：事件→需要全部走 needs_events 自带的映射与上限，
时间→需要走 needs_sleep 自带的系数。唯一由我们选的是“多久算停滞”（STALL_HOURS），
可用 BIOCORE_NEEDS4_STALLED_H 回退；整个接线可用 BIOCORE_NEEDS4=0 关掉。
"""
from __future__ import annotations

import json
import os
from typing import Dict

try:
    from biocore_model import needs_sleep as _sleep
    from biocore_model import needs_events as _events
except Exception:  # pragma: no cover
    _sleep = None
    _events = None

NEED_KEYS = ("belonging", "self_esteem", "control", "meaningful_existence")
FILE = "needs4.json"
DEBT_CAP = 24.0
# 多久没人说话算“停滞”（只用来触发 needs_sleep 的 stalled 分支）。
# 无文献值：取 6 小时因为一轮班/一个白天的沉默已经足够让人感到被放在一边。
STALL_HOURS = float(os.environ.get("BIOCORE_NEEDS4_STALLED_H") or 6.0)


def enabled(value=None) -> bool:
    raw = value
    if raw is None:
        raw = os.environ.get("BIOCORE_NEEDS4") or "0"
    return str(raw).strip().lower() in ("1", "true", "on", "yes")


def _path(store, session_id) -> str:
    import re
    sid = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "s1"))[:60]
    return os.path.join(str(store), "sessions", sid, FILE)


def load(store, session_id) -> Dict[str, float]:
    try:
        with open(_path(store, session_id), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return {}
        return {k: float(v) for k, v in doc.items()
                if isinstance(v, (int, float))}
    except Exception:
        return {}


def save(store, session_id, data) -> bool:
    try:
        path = _path(store, session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: round(float(v), 5) for k, v in (data or {}).items()
                       if isinstance(v, (int, float))}, fh)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def step(store, session_id, now, dims=None, dt_hours=1.0, silence_hours=None):
    """一拍：评价→事件→需要；时间→需要；时间→睡眠债。"""
    if _sleep is None or _events is None:
        return {}
    prev = load(store, session_id)
    base = {k: float(prev.get(k, _sleep.BASE_NEED)) for k in NEED_KEYS}
    if silence_hours is None:
        last = float(prev.get("last_seen_ts") or 0.0)
        silence_hours = max(0.0, (float(now) - last) / 3600.0) if last > 0 else 0.0
    silence_hours = float(max(0.0, silence_hours))
    # 1) 事件→需要：映射、强度、上限全部由 needs_events 自己给
    events = _events.events_from_dims(dims or {})
    after_events, report = _events.apply(base, events)
    # 2) 时间→需要：silence 与 stalled 由 needs_sleep 自己算
    time_events = {"silence_hours": silence_hours,
                   "stalled": bool(silence_hours >= STALL_HOURS)}
    working = dict(after_events)
    working["last_tick_ts"] = float(prev.get("last_tick_ts") or 0.0)
    needs_new = _sleep.need_step(working, time_events,
                                 dt=max(0.0, min(6.0, float(dt_hours))),
                                 now=float(now))
    # 3) 时间→睡眠债：累积与偿还由 needs_sleep 自己算
    debt_prev = float(prev.get("sleep_debt") or 0.0)
    debt_new = _sleep.sleep_debt_step(
        debt_prev, slept_hours=0.0, dt=max(0.0, min(24.0, float(dt_hours))),
        now=float(now), state={"last_debt_ts": float(prev.get("last_debt_ts") or 0.0)})
    out = {k: float(needs_new.get(k, _sleep.BASE_NEED)) for k in NEED_KEYS}
    out["sleep_debt"] = float(debt_new.get("debt") or 0.0)
    out["debt_norm"] = round(min(1.0, out["sleep_debt"] / DEBT_CAP), 4)
    out["last_tick_ts"] = float(needs_new.get("last_tick_ts") or now)
    out["last_debt_ts"] = float(debt_new.get("last_debt_ts") or now)
    out["last_seen_ts"] = float(now)
    out["silence_hours"] = round(silence_hours, 3)
    out["events"] = [row.get("kind") for row in (report.get("events") or [])]
    out["event_deltas"] = {k: float(v) for k, v in
                          (report.get("deltas") or {}).items()}
    # 4) 四需要→行为倾向：由 needs_events 自己给（本次只落盘，不接进行为）
    bias = _events.behavior_bias(needs_new)
    for key in ("seek_contact", "self_assert", "ask_clarify", "share_meaning"):
        out["bias_" + key] = float(bias.get(key) or 0.0)
    save(store, session_id, out)
    return out
