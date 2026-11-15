# -*- coding: utf-8 -*-
"""从 learn_log.jsonl 拟合"评价七维 → 状态增量"的权重，写回 store。

这是学习闭环缺失的那一环：log_learn 一直在记 dims → before/after，
但没有任何代码读它。这里用最小二乘把观测拟合成一张 7×N 的权重表，
appraise() 接受 weights 参数，pipeline 读文件后传进去即可生效。

只读日志、只写一个权重文件；不训练模型、不调模型。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

DIMS = ("goal", "blame", "control", "certainty", "novelty", "involve", "hit")
OUTPUTS = ("v", "a", "d", "social", "trust", "stress", "self_esteem")
# 日志里记的是这些字段名；没记的通道（如 d/trust）拟合不出来，直接跳过。
KEY_OF = {"v": "valence", "a": "arousal", "d": "dominance",
          "social": "social_drive", "trust": "trust",
          "stress": "stress", "self_esteem": "self_esteem"}
WEIGHTS_FILE = "appraisal_weights.json"
LEARN_FILE = "learn_log.jsonl"


def log_path(store, session_id):
    """与 mindbase_bridge 同口径：<store>/sessions/<sid>/learn_log.jsonl。"""
    import re
    sid = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "s1"))[:60]
    return os.path.join(str(store), "sessions", sid, LEARN_FILE)


def weights_path(store):
    return os.path.join(str(store), WEIGHTS_FILE)


def read_rows(store, session_id=None) -> List[dict]:
    """读一个会话或全部会话的观测行。"""
    paths = []
    if session_id:
        paths.append(log_path(store, session_id))
    else:
        base = os.path.join(str(store), "sessions")
        if os.path.isdir(base):
            for name in os.listdir(base):
                p = os.path.join(base, name, LEARN_FILE)
                if os.path.exists(p):
                    paths.append(p)
    rows = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue
    return rows


def _solve(a, b):
    """小型正规方程（7×7）。返回 None 表示奇异。"""
    n = len(a)
    m = [list(map(float, a[i])) + [float(b[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-9:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        for j in range(col, n + 1):
            m[col][j] /= pv
        for r in range(n):
            if r == col:
                continue
            f = m[r][col]
            if f:
                for j in range(col, n + 1):
                    m[r][j] -= f * m[col][j]
    return [m[i][n] for i in range(n)]


def fit(rows, min_rows=8) -> Optional[Dict[str, Dict[str, float]]]:
    """按输出通道分别拟合；样本不足或奇异就返回 None。"""
    usable = [r for r in rows if isinstance(r.get("dims"), dict)
              and isinstance(r.get("before"), dict) and isinstance(r.get("after"), dict)]
    if len(usable) < int(min_rows):
        return None
    out = {}
    for channel in OUTPUTS:
        key = KEY_OF.get(channel, channel)
        xs, ys = [], []
        seen = False
        for row in usable:
            dims = row["dims"]
            before = row["before"]
            after = row["after"]
            if key not in before and key not in after:
                continue
            seen = True
            delta = float(after.get(key) or 0.0) - float(before.get(key) or 0.0)
            xs.append([float(dims.get(d) or 0.0) for d in DIMS])
            ys.append(delta)
        if not seen:
            continue
        # 正规方程 A^T A w = A^T y
        n = len(DIMS)
        ata = [[sum(x[i] * x[j] for x in xs) for j in range(n)] for i in range(n)]
        aty = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(n)]
        w = _solve(ata, aty)
        if w is None:
            continue
        out[channel] = {d: round(val, 4) for d, val in zip(DIMS, w)}
    return out or None


def save(store, table) -> str:
    path = weights_path(store)
    doc = {"version": 1, "weights": table}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load(store) -> Dict[str, Dict[str, float]]:
    """读已拟合的权重；没有或坏了就返回空。"""
    path = weights_path(store)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        table = doc.get("weights") if isinstance(doc, dict) else None
        return table if isinstance(table, dict) else {}
    except Exception:
        return {}


def flatten(table) -> Dict[str, float]:
    """{"v": {"goal": 0.1}} → {"goal.v": 0.1}，供 appraise(weights=...) 使用。"""
    out = {}
    for channel, row in (table or {}).items():
        if not isinstance(row, dict):
            continue
        for dim, value in row.items():
            try:
                out["%s.%s" % (dim, channel)] = float(value)
            except Exception:
                continue
    return out
