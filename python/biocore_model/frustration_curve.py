# -*- coding: utf-8 -*-
"""Amsel frustration curve, variable-ratio schedule, goals, autonomy/competence."""
from __future__ import annotations

import random

BASE_INTERVAL_MIN = 60.0
BURST_P = 0.05
BURST_COOLDOWN_S = 6 * 3600.0


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def frustration_mult(n):
    """Inverted-U: invigoration -> plateau -> extinction (Amsel 1958)."""
    try:
        count = max(0, int(n or 0))
    except Exception:
        count = 0
    if count <= 0:
        return 1.0
    if count <= 3:
        return round(1.0 + 0.04 * count, 4)
    if count <= 8:
        return round(1.16 + 0.01 * count, 4)
    return round(max(0.9, 1.24 - 0.02 * (count - 8)), 4)


def phase(n):
    count = max(0, int(n or 0))
    if count <= 3:
        return "invigoration"
    if count <= 8:
        return "plateau"
    return "extinction"


def next_interval(base_min=BASE_INTERVAL_MIN, rng=None):
    r = rng or random
    return round(float(base_min) * r.uniform(0.6, 1.6), 3)


def next_allowed(now, base_min=BASE_INTERVAL_MIN, rng=None):
    return float(now) + next_interval(base_min, rng) * 60.0


def schedule_tick(state, now, base_min=BASE_INTERVAL_MIN, rng=None):
    """Variable interval + rare burst that skips the wait (6h cooldown)."""
    state = dict(state or {})
    r = rng or random
    cooldown = float(state.get("burst_cooldown_until", 0.0) or 0.0)
    burst = False
    if float(now) >= cooldown and r.random() < BURST_P:
        burst = True
        state["burst_cooldown_until"] = float(now) + BURST_COOLDOWN_S
        state["next_allowed_ts"] = float(now)
    else:
        state["next_allowed_ts"] = next_allowed(now, base_min, r)
    state["burst"] = burst
    state.setdefault("wait_blocked", 0)
    return state


def wait_blocked(state, now):
    state = dict(state or {})
    if float(now) < float(state.get("next_allowed_ts", 0.0) or 0.0):
        state["wait_blocked"] = int(state.get("wait_blocked", 0) or 0) + 1
        state["last_wait_block_ts"] = float(now)
    return state


def expectation_unmet(over_threshold=False, spoke=False, has_channel=False):
    """B14 (Amsel 传统): 挫折来自"预期奖励未到"，不是"被拒绝"。

    预期存在 = 这一拍她**想开口**（spoke）；预期兑现 = 有真实发送通道
    （has_channel，词真的发出去了）。只是"过线"（over_threshold）是状态不好，
    不是未兑现的预期 —— 它单独出现时不算一次。
    """
    if not spoke:
        return False
    return not bool(has_channel)


def counts_as_attempt(over_threshold=False, spoke=False, has_channel=False,
                      expect_mode=False):
    """这一拍要不要给 frustration_n 加一。

    expect_mode=False 是旧口径（over 或 spoke 都算），保证开关默认关时
    数值逐字不变；expect_mode=True 换成 B14 的"有预期未兑现"。
    """
    if expect_mode:
        return expectation_unmet(over_threshold, spoke, has_channel)
    return bool(over_threshold or spoke)


def goals_tick(goals, now, hours=1.0):
    """Progress stalls raise frustration; near-deadline unfinished raises anxiety."""
    rows = []
    for goal in goals or []:
        row = dict(goal or {})
        row.setdefault("progress", 0.0); row.setdefault("status", "active")
        if row.get("status") != "active":
            rows.append(row); continue
        row["frustration_delta"] = round(0.02 * max(0.0, float(hours)), 4)
        deadline = float(row.get("deadline_ts", 0.0) or 0.0)
        row["anxiety_delta"] = 0.03 if deadline and 0 < (deadline - float(now)) <= 86400 else 0.0
        rows.append(row)
    return rows


def goal_completed(goal):
    row = dict(goal or {})
    row.update({"status": "done", "progress": 1.0})
    return {"goal": row, "joy": 0.05, "confidence": 0.03}


def needs_update(autonomy=0.5, competence=0.5, allowed=True, helpful=False, wrong=False):
    autonomy = clamp(autonomy + (0.03 if allowed else -0.03))
    competence = clamp(competence + (0.03 if helpful else (-0.05 if wrong else 0.0)))
    return {"autonomy_need": round(autonomy, 4), "competence_need": round(competence, 4),
            "suppress_bonus": round(0.2 if autonomy < 0.4 else 0.0, 3),
            "confidence_delta": round(-0.05 if competence < 0.4 else 0.0, 4)}


def view(n, schedule=None, goals=None, needs=None):
    schedule = dict(schedule or {})
    return {"frustration": {"n": int(n or 0), "phase": phase(n), "drive_mult": frustration_mult(n)},
            "schedule": {"next_allowed_ts": round(float(schedule.get("next_allowed_ts", 0.0) or 0.0), 1),
                         "wait_blocked": int(schedule.get("wait_blocked", 0) or 0),
                         "burst": bool(schedule.get("burst", False))},
            "goals": list(goals or []),
            "needs": dict(needs or {})}
