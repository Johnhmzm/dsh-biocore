# -*- coding: utf-8 -*-
"""选择性解压：召回前用规则决定"这一轮读到哪一层"（SeDeM 式）。

只读查询文本，不调模型、不写库、不改表。三档：

- gist  （默认，最省）：命题层。memory_search 用 units.gist；缺命题时退到
  条目摘要或命中句的首句压缩。day_recall 用日总结（当天最压缩的一层），
  没有日总结时退回事件概述，并在返回值里标 depth_fallback。
- event （展开一层）：事件总结层。命中事件的 events.overview（memory_search
  的 event_summary 字段 / day_recall 的事件概述行）。
- raw   （最深处）：原文层。原始句子/成员原文，只在明确要"原文/原话/逐字"
  时才给。

判定顺序：显式 depth 参数 > 原文词 > 事件信号 > 默认 gist。
原文词：原文、原话、逐字、一字不差、原句、原样、verbatim。
事件信号：编号/ID（M0001、EV…、session-…）、文件路径（含 / 或 \ 且带扩展名）、
引号里的原话片段、以及"具体/怎么写的/哪一句/哪句话/具体内容/细节"这类词。
"""
from __future__ import annotations

import re

DEPTHS = ("gist", "event", "raw")
DEFAULT_DEPTH = "gist"
DEPTH_LABELS = {"gist": "命题层", "event": "事件总结层", "raw": "原文层"}

_RAW_WORDS = ("原文", "原话", "逐字", "一字不差", "原句", "原样", "verbatim")
_EVENT_WORDS = ("具体", "怎么写的", "哪一句", "哪句话", "具体内容", "具体说",
                "细节", "发生了什么", "当时说")

# 编号 / ID：M0001、EV001、EV-12、session-xxxx
_ID_PATTERNS = (
    ("M-NUMBER", re.compile(r"(?<![0-9A-Za-z])M[0-9]{3,}(?![0-9A-Za-z])")),
    ("EVENT-ID", re.compile(r"(?<![0-9A-Za-z])EV[0-9A-Za-z_-]*[0-9][0-9A-Za-z_-]*(?![0-9A-Za-z])")),
    ("SESSION-ID", re.compile(r"(?<![0-9A-Za-z])session-[0-9A-Za-z][0-9A-Za-z_-]{2,}(?![0-9A-Za-z])")),
)

# 文件路径：含 / 或  且带扩展名（Windows 盘符路径也算）。
_PATH_RE = re.compile(
    r'(?:^|(?<=[\s，。；;：:、（(，,"\']))'
    r'(?:[A-Za-z]:)?[^\s"\'<>|]{0,180}?[\\/][^\s"\'<>|]*\.[A-Za-z0-9]{1,8}'
    r'(?=$|[\s,，。；;：:、）)\"\'\]])')

# 引号里的原话片段（英文直引号 / 中文引号）。
_QUOTE_RES = (
    re.compile(r"[“「『]([^”」』\n]{1,200})[”」』]"),
    re.compile(r'"([^"\n]{1,200})"'),
)


def _event_signals(text):
    """返回命中的事件层信号（按出现顺序、去重）。"""
    signals = []
    for kind, pattern in _ID_PATTERNS:
        for match in pattern.finditer(text):
            signals.append({"kind": "id", "token": kind,
                            "match": match.group(0)})
    path = _PATH_RE.search(text)
    if path and not path.group(0).lower().startswith(("http://", "https://")):
        signals.append({"kind": "path", "match": path.group(0)})
    for quote_re in _QUOTE_RES:
        found = quote_re.search(text)
        if found:
            snippet = next((g for g in found.groups() if g), "").strip()
            if snippet:
                signals.append({"kind": "quote", "match": snippet[:80]})
            break
    lowered = text.lower()
    for word in _EVENT_WORDS:
        if word in text:
            signals.append({"kind": "keyword", "match": word})
            break
    return signals


def decide_depth(query, depth=None):
    """规则判定该读到哪一层。

    返回 {"depth", "reason", "signals"}：
    - depth: "gist" | "event" | "raw"
    - reason: 一句可直接展示的说明（中文，含命中的信号）
    - signals: 命中的信号列表（gist 时为空）
    """
    if isinstance(depth, str) and depth.strip().lower() in DEPTHS:
        value = depth.strip().lower()
        return {"depth": value,
                "reason": "显式指定：%s" % DEPTH_LABELS[value],
                "signals": []}
    text = str(query or "").strip()
    if not text:
        return {"depth": DEFAULT_DEPTH, "reason": "默认：无查询，读命题层",
                "signals": []}
    lowered_text = text.lower()
    for word in _RAW_WORDS:
        if word in text or word in lowered_text:
            return {"depth": "raw",
                    "reason": "明确要原文：%s" % word,
                    "signals": [{"kind": "raw_word", "match": word}]}
    signals = _event_signals(text)
    if signals:
        first = signals[0]
        detail = first.get("match") or first.get("token") or ""
        if first["kind"] == "id":
            reason = "事件层：编号 %s" % detail
        elif first["kind"] == "path":
            reason = "事件层：文件路径 %s" % detail
        elif first["kind"] == "quote":
            reason = "事件层：引号原话片段 %s" % detail
        else:
            reason = "事件层：具体化词 %s" % detail
        return {"depth": "event", "reason": reason, "signals": signals}
    return {"depth": DEFAULT_DEPTH, "reason": "默认：无展开信号，读命题层",
            "signals": []}


def label(depth):
    return DEPTH_LABELS.get(str(depth or ""), "")
