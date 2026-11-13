# -*- coding: utf-8 -*-
"""Context summary: the first link of the memory chain.

The chain is 上下文总结 -> 命题切分 -> 句子 -> 事件 -> 事件原文. The proposition
layer (_unit_texts/_ensure_units), the sentence layer and the event layer all
exist; what the recall seed lacked was the summary: it used to be the last user
line and nothing else, so a follow-up ("那后来呢", "为什么不删干净") had no
content to retrieve with.

No model is called. The summary is built from what the write path already
produced -- each row's concepts (CJK bigrams) and role -- plus, when they exist,
the claim subjects the event layer bound. A token that appears in two or more of
the recent rows is a topic; one row is an accident.
"""
from __future__ import annotations

WINDOW_DEFAULT = 6
TOPIC_LIMIT = 12
SUMMARY_MAX_CHARS = 160
SEED_LIMIT = 3        # the raw question is always route 0


def recent_rows(entries, window=WINDOW_DEFAULT):
    """The last few rows that still carry text, oldest first."""
    rows = [row for row in (entries or [])
            if isinstance(row, dict) and str(row.get("text") or "").strip()]
    if window and int(window) > 0:
        rows = rows[-int(window):]
    return rows


def _concepts(row):
    values = row.get("concepts")
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value or "").strip()]


def topics(rows, limit=TOPIC_LIMIT):
    """Concepts shared by at least two rows, most repeated first."""
    counts = {}
    for row in rows or []:
        for token in set(_concepts(row)):
            if len(token) < 2:
                continue
            counts[token] = counts.get(token, 0) + 1
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], -len(pair[0]), pair[0]))
    return [token for token, count in ordered if count >= 2][:max(0, int(limit or 0))]


def _subject_names(rows, limit=6):
    """Claim subjects the recent rows carry, in order, deduplicated."""
    try:
        from ..index import claim_entities as _ce
    except Exception:
        return []
    out = []
    for row in rows or []:
        for name in _ce.entry_names(row):
            if name not in out:
                out.append(name)
    return out[:max(0, int(limit or 0))]


def summary(entries, window=WINDOW_DEFAULT, limit=SUMMARY_MAX_CHARS, subjects=None):
    """One short line: what is being talked about, plus the last user line."""
    rows = recent_rows(entries, window)
    if not rows:
        return ""
    tokens = topics(rows)
    last_user = ""
    for row in reversed(rows):
        if str(row.get("role") or "user") == "user":
            last_user = str(row.get("text") or "").strip()
            break
    parts = []
    if tokens:
        parts.append("主题 " + " ".join(tokens))
    names = [str(name).strip() for name in (subjects if subjects is not None
                                            else _subject_names(rows))
             if str(name or "").strip()]
    if names:
        parts.append("涉及 " + " ".join(names[:6]))
    if last_user:
        parts.append(last_user[:80])
    if not parts:
        return ""
    return "；".join(parts)[:max(0, int(limit or 0))]


def seed_queries(query, entries, window=WINDOW_DEFAULT, limit=SEED_LIMIT,
                 subjects=None):
    """[user_text, summary, topic bag] -- the raw question stays route 0.

    The topic bag is the summary without its labels: when the pool is scored
    lexically, every extra character in the query is a character that cannot
    match, so the dense route is kept next to the readable one and the caller
    takes the best of them.
    """
    text = str(query or "").strip()
    out = [text] if text else []
    rows = recent_rows(entries, window)
    if not rows:
        return out
    full = summary(entries, window=window, subjects=subjects)
    dense = " ".join(topics(rows)[:8]).strip()
    for piece in (full, dense):
        if piece and piece not in out:
            out.append(piece)
    return out[:max(1, int(limit or 1))]
