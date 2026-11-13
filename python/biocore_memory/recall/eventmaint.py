# -*- coding: utf-8 -*-
"""Event maintenance decisions: when to close an event, and how to split one.

Pure decisions only. The caller owns the model call, the claims parse, the state
update, the journal and the write; this module answers two questions: does this
turn start a new event, and -- when a note does not cover its span -- where does
the span split.

The threshold is mean - 1 sigma, not a low quantile: a low quantile sits near the
body of a stable conversation, so a session where nothing changes gets a bar it
keeps tripping (fragments). mean - sigma sits below the body -- only a real drop
closes -- and a split distribution puts it between the two peaks.
"""
from __future__ import annotations

import math

from ..event import segment_adaptive as _SA

THR_FLOOR = 0.0
THR_CEIL = 1.0


def _quantile(values, q):
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return ordered[index]


def sim_threshold(series, *, override=None, default=0.5, lo=0.0, hi=1.0):
    """Closing threshold for one session, with its provenance.

    Returns {"value", "source", "n", "p20", "p50", "p80"}; source is "override",
    "default" (no samples) or "adaptive". Logging stays with the caller: the text
    differs per source and must stay exactly as it was.
    """
    if override not in (None, ""):
        try:
            value = float(override)
        except Exception:
            value = float(default)
        return {"value": max(THR_FLOOR, min(THR_CEIL, value)), "source": "override",
                "n": 0, "p20": 0.0, "p50": 0.0, "p80": 0.0}
    rows = [float(x) for x in (series or []) if isinstance(x, (int, float))]
    if not rows:
        return {"value": float(default), "source": "default",
                "n": 0, "p20": 0.0, "p50": 0.0, "p80": 0.0}
    try:
        mean = sum(rows) / float(len(rows))
        var = sum((row - mean) ** 2 for row in rows) / float(len(rows))
        value = mean - math.sqrt(var)
    except Exception:
        value = float(default)
    value = max(float(lo), min(float(hi), float(value)))
    return {"value": value, "source": "adaptive", "n": len(rows),
            "p20": _quantile(rows, 0.20), "p50": _quantile(rows, 0.50),
            "p80": _quantile(rows, 0.80)}


def should_close(buf, item, sim, gap, thr, *, close_chars, min_turns, max_turns,
                 gap_s):
    """Why this turn should start a new event, or "" to keep accumulating.

    Order matters: a topic change or a long silence closes immediately; the turn
    ceiling closes an event that would never end otherwise; the character budget
    closes once the event holds information, gated by the turn floor (a single
    line is not an event, however long it is).
    """
    rows = list(buf or [])
    if not rows:
        return ""
    if sim < float(thr):
        return "topic"
    if gap > float(gap_s):
        return "gap"
    turns = len(rows)
    if max_turns > 0 and turns >= int(max_turns):
        return "max_turns"
    if close_chars > 0 and turns >= max(1, int(min_turns)):
        chars = sum(len(str(row.get("text") or "")) for row in rows)
        chars += len(str((item or {}).get("text") or ""))
        if chars >= int(close_chars):
            return "chars"
    return ""


def plan_adaptation(turn_vectors, note_vector, *, span, chars, max_turns, max_chars,
                    cover_min=_SA.COVER_MIN, min_turns=_SA.MIN_TURNS,
                    depth=0, max_depth=6):
    """Keep the closed event, close it whole, or bisect at the largest jump.

    Mirrors the inline decision: too deep or too short never bisects; a covered
    note that is not oversized keeps the event; otherwise the span is cut at the
    largest adjacent similarity drop (cut <= 0 or >= len means "no usable cut").
    """
    vectors = list(turn_vectors or [])
    if depth >= max_depth or len(vectors) < 2 * int(min_turns):
        return {"action": "close_whole", "reason": "depth_or_short", "depth": depth}
    covered = _SA.coverage(note_vector, vectors) if note_vector else 1.0
    too_big = ((max_turns > 0 and span > max_turns) or chars > max_chars)
    if covered >= cover_min and not too_big:
        return {"action": "keep", "reason": "covered", "coverage": covered,
                "depth": depth}
    cut = _SA.largest_jump(vectors)
    if cut <= 0 or cut >= len(vectors):
        return {"action": "close_whole", "reason": "no_cut", "coverage": covered,
                "depth": depth}
    return {"action": "bisect", "cut": cut, "coverage": covered, "depth": depth}
