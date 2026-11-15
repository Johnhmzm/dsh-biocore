# -*- coding: utf-8 -*-
"""话题封闭：提过的事收住，别一拍一拍反复捡起同一个话题。

现状缺口（带文件:行）：
  - L5 每拍从 rest/share/muse 里挑一个话题（`pipeline.py` 的 `mind_wander.choose`），
    没有"提过就收住"的状态 —— 同一个话题会一拍接一拍被重新捡起；
  - `biocore_model/exit_or_silence.py` 是 per-turn 四选一（respond/delay/silent/
    end_topic），**没有记忆**：`end_topic` 只表示"这一拍短说收尾"，下一拍照样起念；
    它的 `silent` 与身体自己那一拍的决定（`decision.action == "silence"`）是同一件事。

本模块只出数、不写句子：{closed, until, repeat, factor}。
开关：`BIOCORE_TOPIC_CLOSURE`（缺省 0）。关着时 factor 恒 1.0、不落盘。
参数（**无文献值，我们的选择 + 回退开关**）：
  HOLD_S = 1800   同一话题提过之后 30 分钟内不再主动提起（`BIOCORE_TOPIC_HOLD_S`）
  FACTOR = 0.25   封闭期内 hazard 乘子（不是 0：憋着的事仍可能漏出来；`BIOCORE_TOPIC_CLOSED_FACTOR`）

文献方向（AFFECT-LIT §15）：Schegloff & Sacks 1973（收尾是双方协商的状态转移，
不是一个 turn 的选择）；Nolen-Hoeksema, Wisco & Lyubomirsky 2008（同一内容反复反刍有害）。
"""
from __future__ import annotations

import json
import os
import time

FILE = "topic_closure.json"
HOLD_S = float(os.environ.get("BIOCORE_TOPIC_HOLD_S") or 1800.0)
FACTOR = float(os.environ.get("BIOCORE_TOPIC_CLOSED_FACTOR") or 0.25)
MAX_TOPICS = 32


def enabled(value=None) -> bool:
    raw = value if value is not None else os.environ.get("BIOCORE_TOPIC_CLOSURE")
    return str(raw or "0").strip().lower() in ("1", "true", "on", "yes")


def _path(store, session_id) -> str:
    import re
    sid = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "s1"))[:60]
    return os.path.join(str(store), "sessions", sid, FILE)


def load(store, session_id) -> dict:
    try:
        with open(_path(store, session_id), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


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


def _topics(doc) -> dict:
    rows = doc.get("topics") if isinstance(doc, dict) else None
    return rows if isinstance(rows, dict) else {}


def view(store, session_id, key, now=None) -> dict:
    """{closed, until, repeat, factor}：只读，不写盘。"""
    stamp = float(now if now is not None else time.time())
    name = str(key or "")
    row = _topics(load(store, session_id)).get(name)
    row = row if isinstance(row, dict) else {}
    until = float(row.get("until") or 0.0)
    closed = bool(name) and until > stamp
    return {"closed": closed, "until": round(until, 3),
            "repeat": int(row.get("repeat") or 0),
            "factor": float(FACTOR if closed else 1.0)}


def factor(store, session_id, key, now=None) -> float:
    """封闭期内给 hazard 的乘子；开着但没记录过就是 1.0。"""
    if not enabled():
        return 1.0
    return view(store, session_id, key, now)["factor"]


def mark(store, session_id, key, now=None) -> dict:
    """把这个话题记成"刚提过"：封闭到 now+HOLD_S，并累计 repeat。"""
    if not enabled():
        return {"marked": False, "reason": "disabled"}
    stamp = float(now if now is not None else time.time())
    name = str(key or "").strip()
    if not name:
        return {"marked": False, "reason": "no_key"}
    doc = load(store, session_id)
    rows = dict(_topics(doc))
    prev_all = rows.get(name) if isinstance(rows.get(name), dict) else {}
    # 先清掉过期的，别让文件无限长（计数不跟着丢：同一话题再出现时接着数）
    rows = {k: v for k, v in rows.items()
            if isinstance(v, dict) and float(v.get("until") or 0.0) > stamp}
    prev = rows.get(name) if isinstance(rows.get(name), dict) else prev_all
    repeat = int(prev.get("repeat") or 0) + 1
    rows[name] = {"closed_at": round(stamp, 3), "until": round(stamp + HOLD_S, 3),
                  "repeat": repeat}
    if len(rows) > MAX_TOPICS:
        keep = sorted(rows.items(), key=lambda kv: float(kv[1].get("until") or 0.0))
        rows = dict(keep[-MAX_TOPICS:])
    ok = _save(store, session_id, {"schema": 1, "topics": rows,
                                   "updated_at": round(stamp, 3)})
    return {"marked": bool(ok), "key": name, "until": round(stamp + HOLD_S, 3),
            "repeat": repeat}


def reset(store, session_id) -> bool:
    """测试/维护用：清空这个会话的封闭记录（不删文件，写成空表）。"""
    return _save(store, session_id, {"schema": 1, "topics": {},
                                     "updated_at": round(time.time(), 3)})
