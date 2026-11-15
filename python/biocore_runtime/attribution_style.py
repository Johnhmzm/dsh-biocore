# -*- coding: utf-8 -*-
"""解释风格（Weiner 归因 × Abramson/Seligman 归因风格）：跨轮的一贯倾向，不是一个标签。

现状缺口（带文件:行）：
  - `biocore_model/attribution.py:18` 的 `infer` 只看**当前这句**（关键词），
    `perception.py:457` 每轮调一次 → 得到一个 per-turn 标签，渲染进状态块；
  - `attribution.apply`（per-event 的情绪增量）**没有任何调用点**；
  - 没有"她一贯怎么解释这些事"的**状态**：单次的 internal/stable 不会累积，
    所以"同一类事第二次发生"时，她的预期与行动倾向和第一次完全一样。

本模块只补状态，不写句子：
  - 把非中性的 locus/stability 累进 EMA（`internality`/`stability`/`n`）；
  - 给 hazard 一个乘子：内-稳定归因越一贯，开口越少（"说了也没用"的努力撤回）。
    至少 MIN_EVENTS 次之后才算"风格"（Abramson 1978：风格是倾向，不是一次）。

开关：`BIOCORE_ATTRIB_STYLE`（缺省 0）。关着时不落盘、乘子恒 1.0。
参数（**无文献值，我们的选择 + 回退开关**）：
  ALPHA = 0.25（EMA 步长，`BIOCORE_ATTRIB_ALPHA`）
  GAIN  = 0.5 （乘子最多压到 1−0.5=0.5，`BIOCORE_ATTRIB_GAIN`）
  MIN_EVENTS = 2（两次同类归因才算风格）

文献方向（AFFECT-LIT §17）：Weiner 1985（归因→情绪→行为链）；Abramson, Seligman &
Teasdale 1978（解释风格是稳定的倾向；内-稳定-全局的归因导致"努力撤回"）。
"""
from __future__ import annotations

import json
import os
import time

FILE = "attribution_style.json"
ALPHA = float(os.environ.get("BIOCORE_ATTRIB_ALPHA") or 0.25)
GAIN = float(os.environ.get("BIOCORE_ATTRIB_GAIN") or 0.5)
MIN_EVENTS = 2
INTERNAL = ("internal", "内部")
STABLE = ("stable", "稳定")


def enabled(value=None) -> bool:
    raw = value if value is not None else os.environ.get("BIOCORE_ATTRIB_STYLE")
    return str(raw or "0").strip().lower() in ("1", "true", "on", "yes")


def _path(store, session_id) -> str:
    import re
    sid = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "s1"))[:60]
    return os.path.join(str(store), "sessions", sid, FILE)


def _blank() -> dict:
    return {"internality": 0.5, "stability": 0.5, "n": 0, "updated_at": 0.0}


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


def _axis(value, words) -> float:
    """internal/stable → 1，external/unstable → 0，其余（neutral/空）→ -1（不更新）。"""
    text = str(value or "").strip()
    if not text or text == "neutral":
        return -1.0
    if text in words:
        return 1.0
    return 0.0


def observe(store, session_id, attribution, now=None) -> dict:
    """把一次归因累进风格；中性/缺失不动。返回更新后的 view。"""
    if not enabled():
        return {"updated": False, "reason": "disabled"}
    data = attribution if isinstance(attribution, dict) else {}
    locus = _axis(data.get("locus"), INTERNAL)
    stable = _axis(data.get("stability"), STABLE)
    if locus < 0.0 and stable < 0.0:
        return {"updated": False, "reason": "neutral"}
    stamp = float(now if now is not None else time.time())
    doc = load(store, session_id)
    if locus >= 0.0:
        doc["internality"] = round(float(doc.get("internality") or 0.5)
                                   + ALPHA * (locus - float(doc.get("internality") or 0.5)), 4)
    if stable >= 0.0:
        doc["stability"] = round(float(doc.get("stability") or 0.5)
                                 + ALPHA * (stable - float(doc.get("stability") or 0.5)), 4)
    doc["n"] = int(doc.get("n") or 0) + 1
    doc["updated_at"] = round(stamp, 3)
    _save(store, session_id, doc)
    return {"updated": True, **view(store, session_id, now)}


def view(store, session_id, now=None) -> dict:
    doc = load(store, session_id)
    n = int(doc.get("n") or 0)
    internality = float(doc.get("internality") or 0.5)
    stability = float(doc.get("stability") or 0.5)
    factor = 1.0
    if n >= MIN_EVENTS:
        factor = round(max(0.0, min(1.0, 1.0 - GAIN * internality * stability)), 4)
    return {"internality": round(internality, 4), "stability": round(stability, 4),
            "n": n, "factor": factor, "style": "internal_stable" if factor < 0.85 else "other"}


def factor(store, session_id, now=None) -> float:
    """hazard 乘子：风格越"内-稳定"，开口越少；关着/样本不足 → 1.0。"""
    if not enabled():
        return 1.0
    return view(store, session_id, now)["factor"]


def reset(store, session_id) -> bool:
    return _save(store, session_id, dict(_blank(), updated_at=round(time.time(), 3)))
