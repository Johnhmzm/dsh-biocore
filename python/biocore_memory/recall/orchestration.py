# -*- coding: utf-8 -*-
"""Delivery plan: which stage handles this call, in what order.

Pure: no store, no environment, no IO. The plan only fixes the ORDER. Whether a
stage is enabled is asked at that stage's door, because the flags live in the
store and are read where they always were -- a config hot-update between two
stages must still take effect exactly as before (the runtime reads _event_layer_on
twice today for that reason; this module must not collapse it into one read).

Order and semantics, preserved byte-for-byte from _inject_block_core:
  range    explicit turn range in the payload; answers and returns, no similarity
  event    event layer (ranking over stored events, claims + sentences merged)
  sentence per-sentence recall, the only fallback when there are no events
  segment  segment recall (Zacks 2007): the unit is a stretch of a session
  entries  always last: query recall or the recent window, then point-expand,
           cover selection, finally flat rendering
"""
from __future__ import annotations

STAGES = ('range', 'event', 'sentence', 'segment', 'entries')


def pick_delivery(has_range=False, has_query=True, sentence_on=True,
                  segment_on=None, event_layer_on=None):
    """Ordered stage list for one call.

    has_range short-circuits everything (the payload named an address, so the
    call is not a similarity question). The store-backed flags (event_layer_on,
    segment_on) may be passed as None: they are then decided at the stage door,
    which keeps their read position identical to the old inline chain.
    """
    if has_range:
        return ('range',)
    stages = []
    if event_layer_on is not False:
        stages.append('event')
    if has_query and sentence_on:
        stages.append('sentence')
    if segment_on is not False:
        stages.append('segment')
    stages.append('entries')
    return tuple(stages)
