"""Deterministic event appraisal cues; used only as a sensor prior.

Handles simple negation and contrast so "突然就没那么憋屈了" is not read as
negative just because the word 憋屈 appears.
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

POS = ("升职", "加薪", "过了", "一次过", "夸", "开心", "高兴", "真好", "成功",
       "通过", "请吃", "朋友", "猫肯", "趴在腿", "放假", "发工资", "喜欢", "笑",
       "轻松", "舒服", "顺利", "搞定", "赚到", "惊喜", "阳光", "好消息", "想动",
       "陪", "好耶", "不错")
NEG = ("裁员", "被裁", "黄了", "甩锅", "失眠", "难过", "崩溃", "吵架", "被骂",
       "被否", "拒绝", "迟到", "加班", "好累", "烦", "气死", "憋屈", "孤独",
       "生气", "越想越气", "当众", "拍脑袋", "没发作", "委屈", "堵", "无力",
       "潮乎乎", "忘了收", "被说", "累")
CONTRAST = ("但是", "但", "不过", "结果", "反而", "可是", "然而", "却", "突然")
NEGATORS = ("没", "不", "别", "未", "无")


def _negated(clause: str, idx: int) -> bool:
    prefix = clause[max(0, idx - 7):idx].replace("不过", "")
    return any(w in prefix for w in NEGATORS)


def _clauses(text: str):
    parts = re.split(r"([，。,.！!？?；;])", str(text or ""))
    out = []
    buf = ""
    for p in parts:
        if p in "，。,.！!？?；;":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += p
    if buf.strip():
        out.append(buf.strip())
    return out or [str(text or "")]


def appraise_event(text: str) -> Tuple[float, Dict[str, int]]:
    clauses = _clauses(text)
    pos_total = 0.0
    neg_total = 0.0
    contrast_seen = False
    for clause in clauses:
        matched_contrast = any(w in clause for w in CONTRAST)
        weight = 1.6 if (contrast_seen or matched_contrast) else 1.0
        local_pos = 0.0
        local_neg = 0.0
        for w in POS:
            start = 0
            while True:
                i = clause.find(w, start)
                if i < 0:
                    break
                if _negated(clause, i):
                    local_neg += 0.5
                else:
                    local_pos += 1.0
                start = i + max(1, len(w))
        for w in NEG:
            start = 0
            while True:
                i = clause.find(w, start)
                if i < 0:
                    break
                if _negated(clause, i):
                    local_pos += 0.5
                else:
                    local_neg += 1.0
                start = i + max(1, len(w))
        pos_total += weight * local_pos
        neg_total += weight * local_neg
        if matched_contrast:
            contrast_seen = True
    total = pos_total + neg_total
    if total <= 0:
        return 0.0, {"pos": 0, "neg": 0}
    score = (pos_total - neg_total) / total
    return max(-1.0, min(1.0, score)), {"pos": round(pos_total, 2), "neg": round(neg_total, 2)}
