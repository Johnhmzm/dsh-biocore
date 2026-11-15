# -*- coding: utf-8 -*-
"""照顾的代价（compassion_fatigue 的状态层）：反复 CARE → 疲劳升；空闲 → 恢复。

现状缺口（带文件:行）：
  - biocore_model/compassion_fatigue.py 是**纯函数**：要调用方给 consecutive / idle_hours；
  - perception.py:484 与 signals.py:112 都在调它，但 consecutive 传的是 0 或一次性值，
    没有跨拍的“连着照顾了多久 / 闲了多久”的状态 → 疲劳永远算不出累积。
  - 七套系统里 CARE 的读数（mindbase_bridge.system_bias）每拍现算，L5 的由头里
    _reason = 0.35*SEEKING + 0.25*CARE + …，CARE 这一路没有“用久了会钝”的折扣。

本模块只补状态（不写句子）：
  - observe(store, sid, care, now)：CARE ≥ CARE_ON 记“照顾了一拍”（consecutive+1，
    并记录 last_care_ts）；否则 consecutive 按空闲时间回落；
  - factor(store, sid, now)：用模块自带的 fatigue(consecutive, density, idle_hours)
    算出 empathy（floor 0.35），只乘在 L5 由头的 CARE 分量上。

开关：BIOCORE_CARE_FATIGUE（缺省 0）。关着时不落盘、factor 恒 1.0。
参数（模块自带，本轮不改）：CONSECUTIVE_WEIGHT=0.22、DENSITY_WEIGHT=2.2、
RECOVERY_TAU_HOURS=6、EMPATHY_FLOOR=0.35。CARE_ON=0.3 是我们的选择（BIOCORE_CARE_ON，本仓 CARE 的常态约 0.30）。

文献方向（AFFECT-LIT §20）：Figley 的 compassion fatigue（照顾者的耗竭）；
Klimecki & Singer 的 empathic distress vs compassion（前者耗竭、后者不耗竭 —— 我们按“耗竭”那一支接）。
"""
from __future__ import annotations

import json
import os
import time

FILE = "care_fatigue.json"
CARE_ON = float(os.environ.get("BIOCORE_CARE_ON") or 0.3)
DENSITY_ALPHA = 0.25


def enabled(value=None) -> bool:
    raw = value if value is not None else os.environ.get("BIOCORE_CARE_FATIGUE")
    return str(raw or "0").strip().lower() in ("1", "true", "on", "yes")


def _path(store, session_id) -> str:
    import re
    sid = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "s1"))[:60]
    return os.path.join(str(store), "sessions", sid, FILE)


def _num(value, default=0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    return float(default) if out != out else out


def _blank() -> dict:
    return {"consecutive": 0, "density": 0.0, "last_care_ts": 0.0,
            "last_beat_ts": 0.0, "updated_at": 0.0}


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


def observe(store, session_id, care, now=None, negative=None) -> dict:
    """一拍：CARE 高就记一次照顾，否则按空闲时间回落 consecutive。"""
    if not enabled():
        return {"updated": False, "reason": "disabled"}
    stamp = float(now if now is not None else time.time())
    doc = load(store, session_id)
    care_v = max(0.0, min(1.0, _num(care, 0.0)))
    last = float(doc.get("last_beat_ts") or 0.0)
    gap_h = max(0.0, (stamp - last) / 3600.0) if last > 0 else 0.0
    consecutive = int(doc.get("consecutive") or 0)
    if care_v >= CARE_ON:
        consecutive += 1
        doc["last_care_ts"] = round(stamp, 3)
    elif gap_h > 0.0:
        # 空闲：每 6 小时掉一档（与模块的恢复 τ 同量级）
        consecutive = max(0, consecutive - int(gap_h // 6.0))
    if negative is not None:
        # 负性密度只在"正在照顾"时累积；空闲时朝 0 回落（照顾别人时的痛才有代价）
        n = max(0.0, min(1.0, _num(negative, 0.0))) if care_v >= CARE_ON else 0.0
        doc["density"] = round(_num(doc.get("density"), 0.0)
                               + DENSITY_ALPHA * (n - _num(doc.get("density"), 0.0)), 4)
    doc["consecutive"] = consecutive
    doc["last_beat_ts"] = round(stamp, 3)
    doc["updated_at"] = round(stamp, 3)
    _save(store, session_id, doc)
    return {"updated": True, **view(store, session_id, stamp)}


def view(store, session_id, now=None) -> dict:
    doc = load(store, session_id)
    stamp = float(now if now is not None else time.time())
    last_care = float(doc.get("last_care_ts") or 0.0)
    idle_h = max(0.0, (stamp - last_care) / 3600.0) if last_care > 0 else 0.0
    try:
        from biocore_model import compassion_fatigue as _cf
        out = _cf.fatigue(int(doc.get("consecutive") or 0),
                          float(doc.get("density") or 0.0), idle_h)
    except Exception:
        out = {"fatigue": 0.0, "empathy": 1.0, "band": "high", "advice": ""}
    return {"consecutive": int(doc.get("consecutive") or 0),
            "density": round(_num(doc.get("density"), 0.0), 4),
            "idle_hours": round(idle_h, 3),
            "fatigue": float(out.get("fatigue") or 0.0),
            "empathy": float(out.get("empathy") or 1.0),
            "band": str(out.get("band") or "high")}


def factor(store, session_id, now=None) -> float:
    """只乘在 L5 由头的 CARE 分量上；关着 → 1.0（与改动前逐字一致）。"""
    if not enabled():
        return 1.0
    return max(0.0, min(1.0, view(store, session_id, now)["empathy"]))


def reset(store, session_id) -> bool:
    return _save(store, session_id, dict(_blank(), updated_at=time.time()))
