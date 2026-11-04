# -*- coding: utf-8 -*-
"""task-221: one global core state shared by every DSH session.

<dataDir>/core_state.json carries the authoritative affect / body / shared
relationship numbers and the latest interoception. Session state only keeps a
snapshot plus this turn's delta, so a new session or a restart starts from the
same body instead of from zero.

Layout:
    {
      "schema": 1,
      "ts": <epoch seconds of the last write>,
      "v": 0.04, "a": 0.52, "stress": 0.2, "energy": 0.69,
      "social": 0.46, "social_pain": 0.0,
      "trust": 0.5, "closeness": 0.5, "conflict": 0.0,
      "intero": {tension, warmth, heaviness, clarity},
      "history": [{"ts", "v", "tension", "warmth", "heaviness", "clarity"}],
      "migrated_sessions": ["<session id>", ...]
    }

Rules:
- turn_end folds only the delta (never overwrites) through update();
- every write is a locked read-modify-write with an atomic replace, so two
  sessions finishing at the same time never lose a delta;
- migrate_session() merges a legacy session's core fields once per session id;
- the module never imports the host, never calls a model, never logs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = 1
FIELDS = ("v", "a", "stress", "energy", "social", "social_pain",
          "trust", "closeness", "conflict")
RANGES = {
    "v": (-1.0, 1.0), "a": (0.0, 1.0), "stress": (0.0, 1.0),
    "energy": (0.0, 1.0), "social": (0.0, 1.0), "social_pain": (0.0, 1.0),
    "trust": (0.0, 1.0), "closeness": (0.0, 1.0), "conflict": (0.0, 1.0),
}
DEFAULTS = {
    "v": 0.0, "a": 0.5, "stress": 0.2, "energy": 0.6, "social": 0.55,
    "social_pain": 0.0, "trust": 0.5, "closeness": 0.5, "conflict": 0.0,
}
HISTORY_LIMIT = 288
LOCK_TIMEOUT_S = 3.0
LOCK_STALE_S = 10.0
CORE_FILE = "core_state.json"


def _num(value, default=0.0):
    try:
        number = float(value)
    except Exception:
        return default
    return number if number == number else default


def _clamp(value, low, high):
    return max(low, min(high, _num(value, low)))


def path_for(store=None, data_dir=None):
    """<dataDir>/core_state.json; store is <dataDir>/memory when given."""
    if data_dir is not None and str(data_dir).strip():
        return Path(str(data_dir).strip()) / CORE_FILE
    if store is None:
        return None
    text = str(store).strip()
    if not text:
        return None
    base = Path(text)
    # DSH passes <dataDir>/memory; tests/CLI may pass a bare data dir. Only the
    # "memory" leaf moves the file up one level, so two independent stores can
    # never share (and corrupt) one core_state.json.
    return (base.parent if base.name == "memory" else base) / CORE_FILE


def _normalize(raw):
    data = raw if isinstance(raw, dict) else {}
    doc = {"schema": SCHEMA}
    for key in FIELDS:
        low, high = RANGES[key]
        doc[key] = round(_clamp(data.get(key, DEFAULTS[key]), low, high), 4)
    doc["ts"] = round(_num(data.get("ts"), 0.0), 3)
    intero = data.get("intero") if isinstance(data.get("intero"), dict) else None
    if intero is None:
        doc["intero"] = None
    else:
        doc["intero"] = {key: round(_clamp(intero.get(key), 0.0, 1.0), 4)
                         for key in ("tension", "warmth", "heaviness",
                                     "clarity")}
    history = []
    for row in data.get("history") or []:
        if not isinstance(row, dict):
            continue
        point = {"ts": round(_num(row.get("ts"), 0.0), 3)}
        for key in ("v", "tension", "warmth", "heaviness", "clarity"):
            if row.get(key) is not None:
                point[key] = round(_num(row.get(key), 0.0), 4)
        history.append(point)
    doc["history"] = history[-HISTORY_LIMIT:]
    doc["migrated_sessions"] = [str(item) for item in
                                (data.get("migrated_sessions") or [])][-200:]
    return doc


def load(path):
    """Normalized document; missing or bad file -> defaults with ts 0."""
    try:
        if path is None or not Path(path).exists():
            return _normalize({})
        return _normalize(json.loads(Path(path).read_text(encoding="utf-8")))
    except Exception:
        return _normalize({})


def exists(path):
    try:
        return path is not None and Path(path).exists()
    except Exception:
        return False


def core_of(doc):
    """The nine authoritative numbers from a normalized document."""
    doc = doc if isinstance(doc, dict) else {}
    return {key: round(_num(doc.get(key), DEFAULTS[key]), 4) for key in FIELDS}


def _write_atomic(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def _acquire_lock(path):
    lock = Path(str(path) + ".lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    deadline = time.time() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, b"1")
            finally:
                os.close(fd)
            return lock
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            except Exception:
                pass
            if time.time() >= deadline:
                return None  # last-write-wins fallback, still atomic replace
            time.sleep(0.005)
        except OSError:
            if time.time() >= deadline:
                return None
            time.sleep(0.005)


def _release(lock):
    if lock is None:
        return
    try:
        lock.unlink()
    except Exception:
        pass


def _mutate(path, fn):
    """Locked read-modify-write; fn(doc) -> (doc, result) or None to skip."""
    if path is None:
        return None
    lock = _acquire_lock(path)
    try:
        doc = load(path)
        changed = fn(doc)
        if changed is None:
            return None
        doc, result = changed
        doc["schema"] = SCHEMA
        _write_atomic(path, doc)
        return result
    except Exception:
        return None
    finally:
        _release(lock)


def apply(path, deltas=None, absolute=None, now=None, session_id="",
          intero_fn=None, history=True):
    """Fold one turn into the global state; returns the new document."""
    stamp = round(_num(now, time.time()), 3)
    adds = deltas if isinstance(deltas, dict) else {}
    fixed = absolute if isinstance(absolute, dict) else {}

    def _fn(doc):
        prev_ts = _num(doc.get("ts"), 0.0)
        dt_hours = 0.0
        if prev_ts > 0 and stamp > prev_ts:
            dt_hours = max(0.0, min(24.0, (stamp - prev_ts) / 3600.0))
        prev_intero = doc.get("intero")
        for key in FIELDS:
            if key in fixed:
                low, high = RANGES[key]
                doc[key] = round(_clamp(fixed[key], low, high), 4)
            elif key in adds:
                low, high = RANGES[key]
                doc[key] = round(_clamp(_num(doc.get(key), DEFAULTS[key])
                                        + _num(adds.get(key), 0.0),
                                        low, high), 4)
        if callable(intero_fn):
            intero = intero_fn(doc, prev_intero, dt_hours, stamp)
            doc["intero"] = (None if intero is None else
                             {key: round(_clamp(intero.get(key), 0.0, 1.0), 4)
                              for key in ("tension", "warmth", "heaviness",
                                          "clarity")})
        doc["ts"] = stamp
        if history:
            point = {"ts": stamp, "v": doc["v"]}
            if isinstance(doc.get("intero"), dict):
                point.update(doc["intero"])
            rows = [row for row in doc.get("history") or []
                    if isinstance(row, dict)]
            rows.append(point)
            doc["history"] = rows[-HISTORY_LIMIT:]
        if session_id:
            sessions = doc.get("migrated_sessions") or []
            if str(session_id) not in sessions:
                sessions = (sessions + [str(session_id)])[-200:]
            doc["migrated_sessions"] = sessions
        return doc, doc

    return _mutate(path, _fn)


def migrate_session(path, core=None, session_id="", now=None):
    """Merge one legacy session's core values once; returns True when merged."""
    values = core if isinstance(core, dict) else {}
    if not values or path is None:
        return False
    stamp = round(_num(now, time.time()), 3)
    sid = str(session_id or "")

    def _fn(doc):
        sessions = doc.get("migrated_sessions") or []
        if sid and sid in sessions:
            return None
        empty = _num(doc.get("ts"), 0.0) <= 0.0
        if empty:
            for key in FIELDS:
                if key in values:
                    low, high = RANGES[key]
                    doc[key] = round(_clamp(values.get(key), low, high), 4)
            if isinstance(values.get("intero"), dict):
                doc["intero"] = {key: round(_clamp(values["intero"].get(key),
                                                  0.0, 1.0), 4)
                                 for key in ("tension", "warmth", "heaviness",
                                             "clarity")}
            doc["ts"] = round(_num(values.get("ts"), stamp), 3) or stamp
        if sid:
            doc["migrated_sessions"] = (sessions + [sid])[-200:]
        return doc, True

    return bool(_mutate(path, _fn))


def snapshot(path):
    """Read-only view for the /state action."""
    doc = load(path)
    return {
        "ok": True,
        "exists": exists(path),
        "ts": doc.get("ts", 0.0),
        "core": core_of(doc),
        "intero": doc.get("intero"),
        "history": list(doc.get("history") or []),
    }


def points(path, limit=24):
    """Newest history points (oldest first), capped at limit."""
    try:
        rows = load(path).get("history") or []
    except Exception:
        return []
    count = max(1, int(limit))
    return rows[-count:]
