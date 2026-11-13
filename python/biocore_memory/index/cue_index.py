# -*- coding: utf-8 -*-
"""Cue-index build/query (entities/place/time_coarse/event_name/id).

The entity half of the cue comes from the claim subjects the write path bound
(the entry inherits them from its event). The regex is only the last resort, for
Latin handles: that is why a Chinese query used to score 0 on this channel.
"""
from __future__ import annotations

import re

from . import claim_entities as _CE
from .entities import cues, extract_entities, normalize_time

_INDEX_KEYS = ("entities", "place", "time_coarse", "event_name", "id")
_ISO_ANY = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _names_of(entry, existing, vocabulary, text):
    """Entity strings: stored/inherited claim subjects first, regex last."""
    out = []
    sources = []
    for value in existing.get("entities") or []:
        sources.append(value)
    sources.extend(_CE.entry_names(entry))
    if not sources:
        sources.extend(extract_entities(text))
    for value in sources:
        name = str(value or "").strip()
        if len(name) >= 2 and name not in out:
            out.append(name)
    if vocabulary:
        for name in _CE.match_names(text, vocabulary):
            if name not in out:
                out.append(name)
    out.sort(key=lambda name: (-len(name), name))
    return out[:8]


def _entry_cues(entry, vocabulary=None) -> dict:
    """Resolve an entry's cues, preferring what the write path already set."""
    e = entry if isinstance(entry, dict) else {}
    existing = e.get("cue_index") if isinstance(e.get("cue_index"), dict) else {}
    text = " ".join(str(e.get(k) or "") for k in ("text", "title", "event", "event_name"))
    ref = e.get("ts") if isinstance(e.get("ts"), (int, float)) else None
    ents = _names_of(e, existing, vocabulary, text)
    out = {"entities": ents,
           "place": [str(x) for x in (existing.get("place") or [])][:4],
           "time_coarse": str(existing.get("time_coarse") or normalize_time(text, ref)),
           "event_name": str(existing.get("event_name") or (ents[0][:20] if ents
                                                           else str(e.get("title") or "")[:20])),
           "id": ""}
    out["id"] = str(e.get("id") or existing.get("id") or "")
    # the write path stores a month bucket in time_coarse and the exact day in
    # "day": keep both, the scorer needs the finer one to prove a match
    out["day"] = str(existing.get("day") or "")
    return out


def build_index(entries, vocabulary=None) -> dict:
    """Build an inverted cue index from dict-like entries (deterministic)."""
    idx = {k: {} for k in _INDEX_KEYS}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        c = _entry_cues(entry, vocabulary=vocabulary)
        for key in _INDEX_KEYS:
            if key == "id":
                if c["id"]:
                    idx["id"][c["id"]] = c["id"]
                continue
            values = c[key] if isinstance(c[key], list) else ([c[key]] if c[key] else [])
            for token in values:
                idx[key].setdefault(str(token), [])
                if c["id"] and c["id"] not in idx[key][str(token)]:
                    idx[key][str(token)].append(c["id"])
    return idx


def _time_tokens(*values):
    """Days out of cue fields: a real date is one token, not six characters.

    Anything that is not a year/month/day string falls back to its characters,
    which is what the old scorer did for every value -- that is exactly why two
    different dates on the same month looked alike.
    """
    out = set()
    for value in values:
        rows = value if isinstance(value, (list, tuple, set)) else [value]
        for row in rows:
            text = str(row or "").strip()
            if not text:
                continue
            if _ISO_ANY.match(text):
                out.add(text)
            else:
                out.update(text)
    return out


def _time_score(q, e):
    """1.0 when both name the same day, 0.5 for the same month, else 0."""
    qt = _time_tokens(q.get("time_coarse"), q.get("time"), q.get("day"))
    et = _time_tokens(e.get("time_coarse"), e.get("time"), e.get("day"))
    if not qt or not et:
        return 0.0
    if qt & et:
        return 1.0
    qm = {token[:7] for token in qt if len(token) >= 7}
    em = {token[:7] for token in et if len(token) >= 7} | et
    return 0.5 if (qm & em) else 0.0


def cue_score(query, entry, vocabulary=None) -> float:
    """Deterministic 0..1 cue overlap between a query and an entry/cue dict."""
    q = cues(query, vocabulary=vocabulary) if isinstance(query, str) else (
        query if isinstance(query, dict) else {})
    # an entry always gets resolved: it may carry a cue_index that predates the
    # claim subjects, and reusing that as-is is how the entity column stayed
    # empty for every row
    _entry_keys = ("text", "claims", "cue_index", "id", "turn", "ts", "role")
    e = (_entry_cues(entry, vocabulary=vocabulary)
         if isinstance(entry, dict) and any(k in entry for k in _entry_keys)
         else (entry if isinstance(entry, dict) else {}))
    def _overlap(a, b):
        sa, sb = set(a or []), set(b or [])
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / float(len(sa | sb))
    ent = _overlap(q.get("entities"), e.get("entities"))
    place = _overlap(q.get("place"), e.get("place"))
    time = _time_score(q, e)
    name = 1.0 if (q.get("entities") and e.get("event_name")
                   and q["entities"][0] in str(e.get("event_name"))) else 0.0
    return round(max(0.0, min(1.0, 0.45 * ent + 0.25 * place + 0.15 * time + 0.15 * name)), 4)


def query(index, text, limit=5) -> list:
    """Query an index and return <=limit entry ids ordered by cue score."""
    if not isinstance(index, dict):
        return []
    q = cues(text)
    candidates, scored = set(), []
    for key in ("entities", "place"):
        for token in q.get(key) or []:
            candidates.update(index.get(key, {}).get(str(token), []))
    if q.get("time_coarse"):
        candidates.update(index.get("time_coarse", {}).get(q["time_coarse"], []))
    for eid in candidates:
        entry = {"id": eid, "cue_index": {k: ([eid] if k == "id" else v)
                                          for k, v in index.items() if k != "id"}}
        scored.append((cue_score(q, entry), eid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [eid for _s, eid in scored[:max(0, int(limit or 0))]]
