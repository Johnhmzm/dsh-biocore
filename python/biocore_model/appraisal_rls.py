# -*- coding: utf-8 -*-
"""评价矩阵的递归最小二乘（RLS）在线估计。

为什么不是批量最小二乘：观测是一条一条来的，等攒够 8 行（fit_appraisal 的门槛）
再用最小二乘，等于前 7 行白扔；而且批量解在数据非平稳时会一直往回看。
文献上这就是线性状态空间 / ARX 的经典做法：带遗忘因子的 RLS = 指数折价的
最小二乘，和 Kalman 滤波在这一情形下同解（见 docs/AFFECT-LIT.md §10）。

三方并存，互不改写：
  - 内置手调矩阵（appraisal.V2_WEIGHTS）       —— 先验，永远在；
  - 批量最小二乘（appraisal_fit.fit，≥8 行）    —— 对照，原样保留；
  - 本模块的 RLS（从第 1 行起）                 —— 在线估计；
最终用的是收缩后的 w_eff = (1-α)·w_prior + α·w_rls，α = n/(n+k)。
n=0 时 α=0 → 与改动前完全一致（测试钉着）。

状态文件：<store>/appraisal_rls.json
参数：λ 默认 0.98（BIOCORE_RLS_LAMBDA），k 默认 40（BIOCORE_RLS_SHRINK_K）。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

DIMS = ("goal", "blame", "control", "certainty", "novelty", "involve", "hit")
CHANNELS = ("v", "a", "d", "social", "trust", "stress", "self_esteem")
KEY_OF = {"v": "valence", "a": "arousal", "d": "dominance",
          "social": "social_drive", "trust": "trust",
          "stress": "stress", "self_esteem": "self_esteem"}
STATE_FILE = "appraisal_rls.json"
LAMBDA = 0.98
SHRINK_K = 40.0
INIT_P = 1.0
EPS = 1e-9


def lambda_value(value=None) -> float:
    raw = value
    if raw is None:
        raw = os.environ.get("BIOCORE_RLS_LAMBDA") or LAMBDA
    try:
        out = float(raw)
    except Exception:
        return float(LAMBDA)
    return max(0.5, min(1.0, out))


def shrink_k(value=None) -> float:
    raw = value
    if raw is None:
        raw = os.environ.get("BIOCORE_RLS_SHRINK_K") or SHRINK_K
    try:
        return max(1.0, float(raw))
    except Exception:
        return float(SHRINK_K)


def _path(store) -> str:
    return os.path.join(str(store), STATE_FILE)


def load(store) -> Dict[str, object]:
    """{"n": int, "channels": {ch: {"w": [...8], "P": [[...], ...]}}}"""
    try:
        with open(_path(store), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError
        chans = doc.get("channels")
        if not isinstance(chans, dict):
            doc["channels"] = {}
        doc.setdefault("n", 0)
        return doc
    except Exception:
        return {"n": 0, "channels": {}}


def save(store, doc) -> bool:
    try:
        path = _path(store)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _features(dims) -> List[float]:
    data = dims if isinstance(dims, dict) else {}
    return [float(data.get(name) or 0.0) for name in DIMS] + [1.0]


def _prior_row(prior, channel) -> List[float]:
    """先验在这一通道上的 8 维权重（前 7 是 dims，第 8 是偏置=0）。"""
    table = prior if isinstance(prior, dict) else {}
    row = []
    for name in DIMS:
        value = table.get("%s.%s" % (name, channel))
        row.append(float(value) if isinstance(value, (int, float)) else 0.0)
    row.append(0.0)
    return row


def _rls_step(w, P, x, y, lam):
    """一次 RLS：k = Px/(λ + xᵀPx)，w += k(y − wᵀx)，P = (P − kxᵀP)/λ。"""
    n = len(w)
    Px = [sum(P[i][j] * x[j] for j in range(n)) for i in range(n)]
    denom = lam + sum(x[i] * Px[i] for i in range(n))
    if abs(denom) < EPS:
        return w, P
    k = [v / denom for v in Px]
    pred = sum(w[i] * x[i] for i in range(n))
    err = float(y) - pred
    w = [w[i] + k[i] * err for i in range(n)]
    P = [[(P[i][j] - k[i] * Px[j]) / lam for j in range(n)] for i in range(n)]
    return w, P


def update(store, dims, before=None, after=None, prior=None, lam=None):
    """用一行观测做一次 RLS（每个通道各一次）。返回本次的摘要。

    y = after[key] − before[key]，key 由 KEY_OF 给出；缺通道就跳过该通道。
    第一行就开始学（不等 8 行），先验用调用方给的 w_prior 做初值。
    """
    if not isinstance(dims, dict) or not dims:
        return {"ok": False, "reason": "no_dims", "n": int(load(store).get("n") or 0)}
    bef = before if isinstance(before, dict) else {}
    aft = after if isinstance(after, dict) else {}
    x = _features(dims)
    lam_v = lambda_value(lam)
    doc = load(store)
    chans = doc.get("channels") if isinstance(doc.get("channels"), dict) else {}
    updated = []
    for channel in CHANNELS:
        key = KEY_OF[channel]
        if key not in bef and key not in aft:
            continue
        y = float(aft.get(key) or 0.0) - float(bef.get(key) or 0.0)
        entry = chans.get(channel) if isinstance(chans.get(channel), dict) else {}
        w = entry.get("w")
        P = entry.get("P")
        if not (isinstance(w, list) and len(w) == len(x)):
            w = _prior_row(prior, channel)
        if not (isinstance(P, list) and len(P) == len(x)
                and all(isinstance(row, list) and len(row) == len(x) for row in P)):
            P = [[INIT_P if i == j else 0.0 for j in range(len(x))]
                 for i in range(len(x))]
        w, P = _rls_step([float(v) for v in w],
                         [[float(v) for v in row] for row in P], x, y, lam_v)
        chans[channel] = {"w": [round(v, 6) for v in w],
                          "P": [[round(v, 6) for v in row] for row in P]}
        updated.append(channel)
    if not updated:
        return {"ok": False, "reason": "no_channels", "n": int(doc.get("n") or 0)}
    doc["channels"] = chans
    doc["n"] = int(doc.get("n") or 0) + 1
    doc["lambda"] = round(lam_v, 4)
    save(store, doc)
    return {"ok": True, "n": doc["n"], "lambda": round(lam_v, 4),
            "channels": updated}


def alpha(n=None, k=None) -> float:
    """收缩系数 α = n/(n+k)：0 行时是 0（纯先验）。"""
    try:
        count = max(0.0, float(n or 0))
    except Exception:
        count = 0.0
    kk = shrink_k(k)
    return count / (count + kk) if (count + kk) > 0 else 0.0


def current_weights(store, prior=None, k=None) -> Dict[str, float]:
    """把 RLS 的估计摊平成 {"dim.channel": w}（只含已学过的通道）。"""
    doc = load(store)
    out = {}
    chans = doc.get("channels") if isinstance(doc.get("channels"), dict) else {}
    for channel, entry in chans.items():
        w = (entry or {}).get("w") if isinstance(entry, dict) else None
        if not (isinstance(w, list) and len(w) >= len(DIMS)):
            continue
        for i, name in enumerate(DIMS):
            out["%s.%s" % (name, channel)] = float(w[i])
    return out


def effective_weights(store, prior=None, k=None):
    """w_eff = (1−α)·w_prior + α·w_rls。返回 (权重dict, 来源标签)。"""
    doc = load(store)
    n = int(doc.get("n") or 0)
    a = alpha(n, k)
    table = prior if isinstance(prior, dict) else {}
    if a <= 0.0:
        return dict(table), "prior"
    rls = current_weights(store, table, k)
    if not rls:
        return dict(table), "prior"
    out = {key: float(value) for key, value in table.items()
           if isinstance(value, (int, float))}
    for key, value in rls.items():
        base = float(out.get(key) or 0.0)
        out[key] = (1.0 - a) * base + a * float(value)
    return out, "blend(n=%d,alpha=%.3f)" % (n, a)
