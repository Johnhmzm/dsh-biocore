# -*- coding: utf-8 -*-
"""Event packing: fold short events into neighbours and give events stable ids.

Pure functions only -- no store, no environment, no file. The runtime supplies the
centroid function and the id knobs; this module decides how a short event joins
its neighbour and what an event is called.

An event id is a digest of its span, never an index: references to an event
(outline windows, claim evidence, world entries) must survive a rebuild. The
fallback below is a dead guard -- int() of start/end does not raise in practice --
kept because removing it buys nothing and could change behaviour if a malformed
event ever reaches here.
"""
from __future__ import annotations

import hashlib

MIN_MEMBERS = 2
ID_PREFIX = 'EV_'
ID_DIGEST_LEN = 8
ID_FALLBACK = 'EV%03d'


def fold_event(dst, src, mean_vector):
    """Fold src into dst: members, span, centroid, note and claims all follow.

    claims are concatenated as-is (no dedupe): a claim repeated across the two
    events is carried twice. That is a known growth risk, recorded rather than
    fixed here, because de-duplicating would change what the claim layer sees.
    """
    dst['entries'] = (dst.get('entries') or []) + (src.get('entries') or [])
    dst['members'] = (dst.get('members') or []) + (src.get('members') or [])
    dst['start'] = min(int(dst.get('start') or 0), int(src.get('start') or 0))
    dst['end'] = max(int(dst.get('end') or 0), int(src.get('end') or 0))
    dst['text'] = ((dst.get('text') or '') + '\n' + (src.get('text') or '')).strip()
    dst['tokens'] = int(dst.get('tokens') or 0) + int(src.get('tokens') or 0)
    dst['vector'] = mean_vector([item.get('vector') for item in dst['entries']])
    note = str(src.get('overview') or '').strip()
    if note and note not in str(dst.get('overview') or ''):
        dst['overview'] = (str(dst.get('overview') or '') + '；' + note).strip('；')
    dst['claims'] = (dst.get('claims') or []) + (src.get('claims') or [])
    return dst


def merge_small_events(events, min_turns=MIN_MEMBERS, mean_vector=None):
    """A one-turn event joins its neighbour: no span shorter than min_turns.

    The head case exists because a short first event has no predecessor: it folds
    forward into the second event instead (this is reachable on a normal path,
    not a guard).
    """
    if len(events or []) < 2:
        return list(events or [])
    out = []
    for ev in events:
        if len(ev.get('members') or []) >= min_turns or not out:
            out.append(dict(ev))
            continue
        fold_event(out[-1], ev, mean_vector)
    if len(out) > 1 and len(out[0].get('members') or []) < min_turns:
        out[1] = fold_event(out[0], out[1], mean_vector)
        out.pop(0)
    return out


def assign_event_ids(events, prefix=ID_PREFIX, digest_len=ID_DIGEST_LEN,
                     fallback=ID_FALLBACK):
    """Give every event an id derived from its span; returns the id list in order.

    事件 id 必须稳定：以前按序号重编号，任何重建都会让已存的引用失效。
    """
    ids = []
    for index, ev in enumerate(events or [], 1):
        try:
            digest = hashlib.sha1(
                ('%s-%s' % (int(ev.get('start') or 0), int(ev.get('end') or 0))).encode()
            ).hexdigest()[:digest_len]
            event_id = '%s%s' % (prefix, digest)
        except Exception:      # dead guard on well-formed events; see module docstring
            event_id = fallback % index
        ev['event_id'] = event_id
        ids.append(event_id)
    return ids
