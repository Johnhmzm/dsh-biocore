# -*- coding: utf-8 -*-
"""Read-only eviction decisions (never deletes, never writes).

eviction_order() keeps the legacy candidate ordering used by the engine.
tick(entries, now) is the M2 read-only decision pass: it partitions entries
into archive / flag / keep for the caller to act on later.

Protection rules (never archived):
- pinned / desktop-anchor style pin;
- self-defining / identity entries;
- explicit promises / open loops / commitments;
- P0/P1 priority entries.

Archive-first signals (deterministic, bounded):
- low salience (importance and event intensity BOTH low, and both measured:
  a signal the store never wrote is unknown, not zero);
- redundancy (duplicate text, later copies first);
- old versions (version chain at/over the configured max, or superseded);
- long unused (no access for >= STALE_DAYS days, when now is supplied);
- otherwise a low eviction score (< ARCHIVE_SCORE).
Protection additionally keeps the newest row of every event that has two or more
rows: the event is the unit recall delivers, so forgetting may thin an event but
never empty it.
Between ARCHIVE_SCORE and FLAG_SCORE the entry is only flagged.
"""
from __future__ import annotations

import os

from ..core.config import MAX_VERSIONS
from ..core.util import clamp01
from .score import eviction_score

ARCHIVE_SCORE = 0.30
FLAG_SCORE = 0.50
# 30 days was the old cut, and on a real store 88.3% of the rows are older than
# it (P20, 766 rows, 181.9-day span): age alone then forgets almost everything.
# 90 days keeps the newest quarter and is a starting calibration, not a fitted
# number -- BIOCORE_EVICT_STALE_DAYS tunes it without a code change.
STALE_DAYS = 90.0
STALE_DAYS_ENV = "BIOCORE_EVICT_STALE_DAYS"
STALE_SECONDS = STALE_DAYS * 86400.0
LOW_SALIENCE = 0.35
REDUNDANCY_ARCHIVE = 0.60
KEEP_NEWEST_PER_EVENT = True
# The eviction score carries `position`, which is a RANK inside the store
# (index/len). A rank is uniformly spread by construction, so with ARCHIVE_SCORE
# as a fixed floor the score branch always cuts the same share of the table no
# matter how young it is: measured 468/1295 (36%) on a 5.9-day-old live store and
# 10/37 on a 4.9-day-old one, every one of them by score, none by age.
# Forgetting is about disuse over time, so the score may only decide for a row
# that is already older than SCORE_MIN_AGE_DAYS; a row from this week is kept.
SCORE_MIN_AGE_DAYS = 7.0
SCORE_MIN_AGE_SECONDS = SCORE_MIN_AGE_DAYS * 86400.0
# L5 schema-based eviction (default OFF: tick behaves exactly as before).
SCHEMA_EVICTION_ENABLED = False
SCHEMA_ARCHIVE = 0.35
CONSEQUENCE_ARCHIVE = 0.40


def _entry_id(entry):
    return str(entry.get("id") or entry.get("memory_id") or "")


def _raw_intensity(entry):
    """Event intensity as written, or None when the store never wrote it.

    The old version folded "missing" into 0.0, which is how every row of a real
    store became "low salience": importance 0.5 (the neutral default) and an
    absent intensity give 0.6*0.5 = 0.30 < 0.35. Unknown is not zero.
    """
    vector = entry.get("event_vector")
    if hasattr(vector, "as_dict"):
        try:
            vector = vector.as_dict()
        except Exception:
            vector = None
    if isinstance(vector, dict) and vector.get("intensity") is not None:
        return clamp01(vector.get("intensity"), 0.0)
    if entry.get("event_intensity") is not None:
        return clamp01(entry.get("event_intensity"), 0.0)
    return None


def _event_intensity(entry):
    intensity = _raw_intensity(entry)
    return 0.0 if intensity is None else intensity


def _salience(entry):
    """0..1 salience; an unmeasured half falls back to neutral (0.5)."""
    importance = entry.get("importance")
    intensity = _raw_intensity(entry)
    imp = 0.5 if importance is None else clamp01(importance, 0.5)
    return clamp01(0.6 * imp + 0.4 * (0.5 if intensity is None else intensity), 0.5)


def _low_salience(entry):
    """Both signals low -- the documented rule -- and both actually measured."""
    importance = entry.get("importance")
    intensity = _raw_intensity(entry)
    if importance is None or intensity is None:
        return False
    return clamp01(importance, 0.5) < LOW_SALIENCE and intensity < LOW_SALIENCE


def _is_protected(entry):
    if entry.get("pin") or entry.get("pinned"):
        return True
    if entry.get("self_defining") or entry.get("identity"):
        return True
    for key in ("promise", "promises", "commitment", "open_loop", "open_loops"):
        if entry.get(key):
            return True
    priority = str(entry.get("priority") or "").upper()
    if entry.get("p0") or entry.get("p1") or priority in ("P0", "P1"):
        return True
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return bool(metadata.get("pin") or metadata.get("pinned")
                or metadata.get("self_defining") or metadata.get("promise"))


def _schema_congruence(entry):
    """Read schema congruence from entry or its event_vector (0..1)."""
    vector = entry.get("event_vector")
    if hasattr(vector, "as_dict"):
        try:
            vector = vector.as_dict()
        except Exception:
            vector = None
    if isinstance(vector, dict) and vector.get("schema_conflict") is not None:
        return clamp01(1.0 - clamp01(vector.get("schema_conflict"), 0.0))
    return clamp01(entry.get("schema_congruence"), 1.0)


def _consequence(entry):
    """0..1 consequence of forgetting; missing means neutral/low."""
    return clamp01(entry.get("consequence"), 0.5)


def _is_old_version(entry):
    if entry.get("superseded") or entry.get("old_version"):
        return True
    versions = entry.get("previous_versions")
    if isinstance(versions, (list, tuple)) and len(versions) >= max(1, int(MAX_VERSIONS)):
        return True
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return bool(metadata.get("superseded") or metadata.get("old_version"))


def _age_seconds(entry, now):
    """Seconds since the row was last of any use.

    last_used is what the recall path writes (biocore_runtime.pipeline
    _mark_access: hits+1 and last_used=now); without it the gate could only see
    the creation timestamp and a memory that is read every day still aged out.
    """
    if not now:
        return 0.0
    last = 0.0
    for key in ("last_recall", "last_access", "last_used", "ts", "created_ts"):
        try:
            last = max(last, float(entry.get(key) or 0.0))
        except Exception:
            continue
    if last <= 0:
        # No access timestamp at all: unknown age counts as long-unused.
        return float(now)
    try:
        return max(0.0, float(now) - last)
    except Exception:
        return 0.0


def effective_last_used(entry, log_stamp=0.0):
    """最后一次"有用"的时间：记忆本体的时间列与使用日志取较大者（兼容老数据）。

    列值来自 last_recall / last_access / last_used / ts / created_ts，
    日志值来自 access_log 的最新 at。两者都没有返回 0.0（未知）。
    """
    last = 0.0
    for key in ("last_recall", "last_access", "last_used", "ts", "created_ts"):
        try:
            last = max(last, float((entry or {}).get(key) or 0.0))
        except Exception:
            continue
    try:
        last = max(last, float(log_stamp or 0.0))
    except Exception:
        pass
    return last


def forget_filter(entries, last_used_by_id=None, now=0.0, stale_seconds=None):
    """「忘」= 回忆期的筛选，不是写操作：返回 (kept, dropped)，库一字不动。

    丢掉的条件只有一条：**能证明**它很久没被用过（>= stale_seconds）。
    未知年龄（既没有时间列也没有日志）一律保留——读路径上错杀不可见，
    宁可取到一条旧记忆，也不静默吞掉它。
    """
    limit = float(stale_seconds if stale_seconds is not None else _stale_seconds())
    stamp = float(now or 0.0)
    kept, dropped = [], []
    log_map = last_used_by_id if isinstance(last_used_by_id, dict) else {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "")
        last = effective_last_used(entry, log_map.get(entry_id, 0.0))
        if last <= 0.0 or not stamp:
            kept.append(entry)
            continue
        age = stamp - last
        if age >= limit:
            dropped.append(entry)
        else:
            kept.append(entry)
    return kept, dropped


def eviction_order(rows):
    """Legacy candidate order: protected/desktop/index-retained first."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        entry = row.get("entry") if isinstance(row.get("entry"), dict) else row
        key = {"id": str(entry.get("id") or ""),
               "score": float(row.get("score", 0.0) or 0.0),
               "pinned": bool(entry.get("pin")),
               "desktop": bool(entry.get("desktop")),
               "index_retained": bool(entry.get("index_retained"))}
        out.append(key)
    out.sort(key=lambda r: (not r["pinned"], not r["desktop"],
                            not r["index_retained"], -r["score"]))
    return out


def _stale_seconds():
    raw = os.environ.get(STALE_DAYS_ENV)
    if raw:
        try:
            return max(1.0, float(raw)) * 86400.0
        except Exception:
            pass
    return STALE_SECONDS


def _newest_per_event(items):
    """Ids of the newest row of every event that has two or more rows."""
    newest = {}
    members = {}
    for entry in items:
        event_id = str(entry.get("event_id") or "")
        if not event_id:
            continue
        members[event_id] = members.get(event_id, 0) + 1
        try:
            stamp = float(entry.get("ts") or entry.get("turn") or 0.0)
        except Exception:
            stamp = 0.0
        current = newest.get(event_id)
        if current is None or stamp > current[0]:
            newest[event_id] = (stamp, _entry_id(entry))
    return {item[1] for event_id, item in newest.items()
            if members.get(event_id, 0) > 1}


def tick(entries, now=0.0):
    """Partition entries into {"archive": [ids], "flag": [ids], "keep": [ids]}.

    Read-only: no entry is mutated or removed. Ordering is deterministic:
    archive/flag by (score ascending, id); keep keeps input order.
    """
    items = [e for e in (entries or []) if isinstance(e, dict)]
    total = max(1, len(items))
    seen_text = set()
    scored = []
    stale_cut = _stale_seconds()
    keep_newest = _newest_per_event(items) if KEEP_NEWEST_PER_EVENT else {}
    for position, entry in enumerate(items):
        entry_id = _entry_id(entry)
        if not entry_id:
            continue
        text = " ".join(str(entry.get("text") or "").split()).lower()
        duplicate = bool(text) and text in seen_text
        if text:
            seen_text.add(text)
        if _is_protected(entry):
            scored.append((entry_id, 1.0, "keep", entry))
            continue
        if entry_id in keep_newest:
            scored.append((entry_id, 1.0, "keep", entry))
            continue
        redundancy = 1.0 if duplicate else (
            1.0 if clamp01(entry.get("redundancy"), 0.0) >= REDUNDANCY_ARCHIVE else 0.0)
        score = eviction_score(entry, now=now,
                               position=position / float(total),
                               redundancy=redundancy)
        stale = _age_seconds(entry, now) >= stale_cut
        low_salience = _low_salience(entry)
        schema_archive = (SCHEMA_EVICTION_ENABLED
                          and _schema_congruence(entry) < SCHEMA_ARCHIVE
                          and _consequence(entry) < CONSEQUENCE_ARCHIVE)
        must_archive = (duplicate or redundancy >= REDUNDANCY_ARCHIVE
                        or _is_old_version(entry) or stale or low_salience
                        or schema_archive)
        aged_enough = _age_seconds(entry, now) >= SCORE_MIN_AGE_SECONDS
        if must_archive or (score < ARCHIVE_SCORE and aged_enough):
            decision = "archive"
        elif score < FLAG_SCORE:
            decision = "flag"
        else:
            decision = "keep"
        scored.append((entry_id, score, decision, entry))
    archive = [item for item in scored if item[2] == "archive"]
    flagged = [item for item in scored if item[2] == "flag"]
    keep = [item[0] for item in scored if item[2] == "keep"]
    archive.sort(key=lambda item: (item[1], item[0]))
    flagged.sort(key=lambda item: (item[1], item[0]))
    return {"archive": [item[0] for item in archive],
            "flag": [item[0] for item in flagged],
            "keep": keep}
