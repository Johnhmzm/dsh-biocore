"""Numeric group autonomy: when should she join a group conversation?

No keyword/topic rules. Uses current drives, stress, energy, group activity,
recent speech and relationship trust.
"""
from __future__ import annotations

import math
import time
from typing import Dict

from .homeostatic import HomeostaticController


class GroupAutonomy:
    def __init__(self, threshold: float = 0.74, cooldown_s: float = 45.0,
                 min_recent_rows: int = 2, window_s: float = 180.0):
        self.threshold = float(threshold)
        self.cooldown_s = float(cooldown_s)
        self.controller = HomeostaticController(threshold=threshold, cooldown_s=cooldown_s)
        self.min_recent_rows = int(min_recent_rows)
        self.window_s = float(window_s)

    def _num(self, x, default=0.5):
        try:
            return float(x)
        except Exception:
            return default

    def adapt(self, engaged: bool) -> float:
        step = 0.015 if engaged else -0.015
        self.threshold = max(0.70, min(0.90, self.threshold - step))
        self.controller.threshold = self.threshold
        return self.threshold

    def score(self, mind, group_ctx, now: float = 0.0,
              last_spoke: float = 0.0, trust_key: str = "",
              min_gap_s: float = 0.0, alertness: float = 0.5,
              allostatic_load: float = 0.0, dyad_gap_s: float = 0.0,
              addressed: bool = False) -> Dict[str, object]:
        now = float(now or time.time())
        rows = []
        try:
            rows = group_ctx.rows_recent(self.window_s, now)
        except Exception:
            rows = []
        activity = min(1.0, len(rows) / 5.0)
        recency = 0.0
        prior_gap = 0.0
        if rows:
            try:
                last_ts = float(rows[-1].get("t", 0.0))
                recency = math.exp(-max(0.0, now - last_ts) / 120.0)
            except Exception:
                recency = 0.0
        if len(rows) >= 2:
            try:
                prior_gap = max(0.0, float(rows[-1].get("t", 0.0)) - float(rows[-2].get("t", 0.0)))
            except Exception:
                prior_gap = 0.0
        interruption = bool(min_gap_s > 0 and len(rows) >= 2 and prior_gap < float(min_gap_s))
        dyad = False
        if dyad_gap_s > 0 and len(rows) >= 2:
            try:
                sp = [str(x.get("speaker", "")) for x in rows[-4:]]
                dyad = bool(len(sp) >= 2 and sp[-1] != sp[-2] and prior_gap < float(dyad_gap_s))
            except Exception:
                dyad = False
        trust = 0.5
        try:
            trust = float(mind.social.trust_for(trust_key or "group"))
        except Exception:
            pass
        d = self.controller.readiness(mind, activity=activity, recency=recency,
                                      trust=trust, now=now, last_action=last_spoke,
                                      alertness=alertness, allostatic_load=allostatic_load)
        if addressed:
            d["readiness"] = round(min(1.0, float(d.get("readiness", 0.0)) + 0.08), 4)
            if not d.get("cooling"):
                d["speak"] = bool(float(d["readiness"]) >= self.threshold)
        enough = len(rows) >= self.min_recent_rows or addressed
        # dyad/interruption are signals, not hard vetoes
        d["threshold"] = self.threshold
        d["speak"] = bool(enough and float(d.get("readiness", 0.0)) >= self.threshold
                          and not d.get("cooling"))
        d["addressed"] = bool(addressed)
        d["interruption"] = interruption
        d["dyad"] = dyad
        d["prior_gap"] = round(prior_gap, 1)
        d["score"] = d.get("readiness", 0.0) - (0.25 if interruption else 0.0)
        d["rows"] = len(rows)
        return d
