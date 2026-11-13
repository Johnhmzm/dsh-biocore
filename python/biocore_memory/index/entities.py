# -*- coding: utf-8 -*-
"""Cue rules for the entity/place/time channels.

Entities come from the claims the write path bound (index.claim_entities), not
from a pattern: a regex can only see Latin handles, which is why the cue channel
scored 0/562 on Chinese questions. The Latin pattern stays as the last resort
for handles and @mentions that no claim ever covered.

Time is not parsed here: the closed-class time grammar lives in
event.timerender (first_date), so a phrase resolves to one date everywhere.
A string the parser does not recognise returns "" -- it never raises, and it
never invents a day.
"""
from __future__ import annotations

import re

from . import claim_entities as _CE
from ..event import timerender as _TR

# Latin handles and @mentions only: a CJK run is not an entity without a claim.
_ENTITY = re.compile(r"@[A-Za-z0-9_\-]{1,30}|[A-Za-z][A-Za-z0-9_'-]{1,30}")
DISABLED_REASON = "no_lexicon"


def normalize_time(text, ref_ts=None) -> str:
    """First date the text names, as YYYY-MM-DD; "" when nothing parses."""
    return _TR.first_date(text, ref_ts)


def extract_entities(text, limit=8, vocabulary=None) -> list:
    """<=limit entity strings: claim-bound names first, Latin handles last."""
    value = str(text or "")
    out = []
    for name in _CE.match_names(value, vocabulary, limit=limit):
        if name not in out:
            out.append(name)
    for hit in _ENTITY.finditer(value):
        token = hit.group(0).strip()
        if len(token) < 2 or token in out:
            continue
        out.append(token[:32])
        if len(out) >= limit:
            break
    return out[:max(0, int(limit or 0))]


def extract_places(text, limit=4) -> list:
    """Places come from the caller (entry["place"]); no place lexicon here."""
    return []


def cues(text, ref_ts=None, vocabulary=None) -> dict:
    """Return a cue dict: entities/place/time_coarse/event_name/id (+ time alias)."""
    value = str(text or "")
    ents = extract_entities(value, vocabulary=vocabulary)
    time_coarse = normalize_time(value, ref_ts)
    event_name = (ents[0][:20] if ents else "")
    return {"entities": ents, "place": extract_places(value),
            "time_coarse": time_coarse, "time": time_coarse,
            "event_name": event_name, "id": ""}