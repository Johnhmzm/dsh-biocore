# -*- coding: utf-8 -*-
"""解释风格（Weiner 归因 × Abramson/Seligman 归因风格）：跨轮的一贯倾向，只从数值推。

现状缺口（带文件:行）：
  - `biocore_model/attribution.py:18` 的 `infer` 只看当前这句（关键词），
    `perception.py:457` 每轮调一次 → per-turn 标签，渲染进状态块；
  - `attribution.apply`（per-event 情绪增量）**没有任何调用点**；
  - 没有"她一贯怎么解释这些事"的**状态**：单次 internal/stable 不累积，
    所以"同一类事第二次发生"时，预期与行动倾向和第一次完全一样。

本模块只补状态，且**不用词表**（核心契约：不靠字面判语义）：
  - internality ← 七维里的 `blame`（0 是自己 .. 1 是别人）→ 1 − blame；
  - stability   ← 事件历史：连续负向事件占比（连续几次都不是偶然 → 稳定）；
  - controllability 只记录、不参与乘子（`control` 已是 0..1）；
  - 给 hazard 一个乘子：内-稳定越一贯，开口越少（"说了也没用"的努力撤回）。
    至少 MIN_EVENTS 次之后才算"风格"（Abramson 1978：风格是倾向，不是一次）。

开关：`BIOCORE_ATTRIB_STYLE`（缺省开，2026-11-16 翻默认；写 0 关）。
关着时不落盘、乘子恒 1.0，L5 逐字回到翻默认前。
参数（**无文献值，我们的选择 + 回退开关**）：
  ALPHA = 0.25（EMA 步长，`BIOCORE_ATTRIB_ALPHA`）
  GAIN  = 0.5 （乘子最多压到 0.5，`BIOCORE_ATTRIB_GAIN`）
  STREAK_FULL = 3（连续三次负向事件 = 稳定度 1.0，`BIOCORE_ATTRIB_STREAK`）
  MIN_EVENTS = 2（两次同类归因才算风格）

文献方向（AFFECT-LIT §17）：Weiner 1985（稳定性改下一次同类事件的主观成功概率）；
Abramson, Seligman & Teasdale 1978（解释风格是稳定的倾向；内-稳定归因把未来无助变慢性）。
"""
from __future__ import annotations

import json
import os
import time

FILE = "attribution_style.json"
ALPHA = float(os.environ.get("BIOCORE_ATTRIB_ALPHA") or 0.25)
GAIN = float(os.environ.get("BIOCORE_ATTRIB_GAIN") or 0.5)
STREAK_FULL = float(os.environ.get("BIOCORE_ATTRIB_STREAK") or 3.0)
MIN_EVENTS = 2
NEGATIVE_DELTA = -0.02


def enabled(value=None) -> bool:
    raw = value if isinstance(value, str) or value is None else None
    if raw is None:
        raw = os.environ.get("BIOCORE_ATTRIB_STYLE")
    return str(raw or "1").strip().lower() in ("1", "true", "on", "yes")


def _path(store, session_id) -> str:
    import re
    sid = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "s1"))[:60]
    return os.path.join(str(store), "sessions", sid, FILE)


def _blank() -> dict:
    return {"internality": 0.5, "stability": 0.0, "control": 0.5,
            "streak": 0, "n": 0, "updated_at": 0.0}


def _num(value, default=0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if out != out:
        return float(default)
    return out


def load(store, session_id) -> dict:
    try:
        with open(_path(store, session_id), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            return _blank()
        out = _blank()
        out.update({k: doc[k] for k in out if k in doc})
        return out
    except Exception:
        return _blank()


def _save(store, session_id, doc) -> bool:
    path = _path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def observe(store, session_id, dims, outcome=None, now=None) -> dict:
    """用一次评价的**数值**累进风格：blame → internality，连续负向 → stability。

    `dims` 就是 appraisal 的七维（blame 0=自己 .. 1=别人，control 0..1）。
    `outcome` 是这一轮的实际结果（通常用 valence 的变化量）：< 0 记为负向事件。
    """
    if not enabled():
        return {"updated": False, "reason": "disabled"}
    data = dims if isinstance(dims, dict) else {}
    raw_blame = data.get("blame")
    if raw_blame is None:
        return {"updated": False, "reason": "no_dims"}
    blame = max(0.0, min(1.0, _num(raw_blame, 0.5)))
    control = max(0.0, min(1.0, _num(data.get("control"), 0.5)))
    stamp = float(now if now is not None else time.time())
    doc = load(store, session_id)
    internality = 1.0 - blame
    doc["internality"] = round(_num(doc.get("internality"), 0.5)
                               + ALPHA * (internality - _num(doc.get("internality"), 0.5)), 4)
    doc["control"] = round(_num(doc.get("control"), 0.5)
                           + ALPHA * (control - _num(doc.get("control"), 0.5)), 4)
    streak = int(doc.get("streak") or 0)
    if outcome is not None and _num(outcome, 0.0) < NEGATIVE_DELTA:
        streak += 1
    elif outcome is not None:
        streak = 0
    doc["streak"] = streak
    # 稳定度是"连续几次"的直接读数，不做平滑：连着三次负向就是稳定度 1.0，
    # 中间出现一次正性就清零。平滑只用在 internality（那才是需要跨事件平均的倾向）。
    doc["stability"] = round(min(1.0, float(streak) / max(1.0, STREAK_FULL)), 4)
    doc["n"] = int(doc.get("n") or 0) + 1
    doc["updated_at"] = round(stamp, 3)
    _save(store, session_id, doc)
    return {"updated": True, **view(store, session_id, now)}


def view(store, session_id, now=None) -> dict:
    doc = load(store, session_id)
    n = int(doc.get("n") or 0)
    internality = max(0.0, min(1.0, _num(doc.get("internality"), 0.5)))
    stability = max(0.0, min(1.0, _num(doc.get("stability"), 0.0)))
    factor = 1.0
    if n >= MIN_EVENTS:
        factor = round(max(0.0, min(1.0, 1.0 - GAIN * internality * stability)), 4)
    return {"internality": round(internality, 4), "stability": round(stability, 4),
            "control": round(_num(doc.get("control"), 0.5), 4),
            "streak": int(doc.get("streak") or 0), "n": n, "factor": factor}


def factor(store, session_id, now=None) -> float:
    """hazard 乘子：风格越"内-稳定"，开口越少；关着/样本不足 → 1.0。"""
    if not enabled():
        return 1.0
    return view(store, session_id, now)["factor"]


def reset(store, session_id) -> bool:
    return _save(store, session_id, dict(_blank(), updated_at=round(time.time(), 3)))
