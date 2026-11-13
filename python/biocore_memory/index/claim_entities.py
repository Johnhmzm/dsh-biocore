# -*- coding: utf-8 -*-
"""Claim subjects as memory entities: the entity channel's source of truth.

A regex can only see Latin handles, which is why the cue channel scored 0/562
on Chinese queries. The write path already binds real subjects ("门口灯箱",
"张姨") when it parses claims; those names are the entities, so this module
reads them instead of guessing.

No lexicon: a name is whatever the claim row already carries in subject /
entity / thread, and a name only *matches* when it literally occurs in the
text. Nothing is stemmed, translated or expanded.
"""
from __future__ import annotations

MIN_NAME = 2          # a single character cannot identify anything
MAX_ENTITIES = 8      # per entry / per query
MAX_VOCABULARY = 800  # per session scan: bounded so a long store stays cheap
_KEYS = ("subject", "entity", "thread")


def claim_names(claims, limit=MAX_ENTITIES):
    """Names bound by a list of claim dicts, longest first, deduplicated."""
    found = []
    for item in claims or []:
        if not isinstance(item, dict):
            continue
        for key in _KEYS:
            name = str(item.get(key) or "").strip()
            if len(name) >= MIN_NAME and name not in found:
                found.append(name)
    found.sort(key=lambda name: (-len(name), name))
    return found[:max(0, int(limit or 0))]


def entry_names(entry, limit=MAX_ENTITIES):
    """Names an entry already carries, or the ones its own claims bind."""
    e = entry if isinstance(entry, dict) else {}
    out = []
    for key in ("entities", "participants"):
        values = e.get(key)
        if isinstance(values, str):
            values = [values]
        for value in values or []:
            name = str(value or "").strip()
            if len(name) >= MIN_NAME and name not in out:
                out.append(name)
    cue = e.get("cue_index") if isinstance(e.get("cue_index"), dict) else {}
    for value in cue.get("entities") or []:
        name = str(value or "").strip()
        if len(name) >= MIN_NAME and name not in out:
            out.append(name)
    if not out:
        out = claim_names(e.get("claims"), limit=limit)
    out.sort(key=lambda name: (-len(name), name))
    return out[:max(0, int(limit or 0))]


def vocabulary(entries, claims=None, limit=MAX_VOCABULARY):
    """Every claim-bound name this store knows, longest first.

    Built from the entries (they inherit their event's claim subjects at write
    time) and, when the caller has them, from the events themselves.
    """
    out = []
    for entry in entries or []:
        for name in entry_names(entry):
            if name not in out:
                out.append(name)
    if claims:
        for name in claim_names(claims, limit=limit):
            if name not in out:
                out.append(name)
    out.sort(key=lambda name: (-len(name), name))
    return out[:max(0, int(limit or 0))]


def match_names(text, names, limit=MAX_ENTITIES):
    """Names that literally occur in the text, longest first (Chinese included)."""
    body = str(text or "")
    if not body:
        return []
    hits = [name for name in (names or []) if name and name in body]
    hits.sort(key=lambda name: (-len(name), name))
    return hits[:max(0, int(limit or 0))]
