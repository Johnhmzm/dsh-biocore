from __future__ import annotations

import random
from collections import deque
from typing import Deque

from .config import MindConfig
from .lexicon import text_affect
from .noise import clip, jaccard
from .types import Appraisal, Event


def occ_emotion(goal_congruence: float, certainty: float, agency: float, coping: float, norm: float) -> str:
    """OCC-style label; heuristic, not a full OCC implementation."""
    if goal_congruence >= 0.0:
        if agency > 0.25:
            return "pride"
        if agency < -0.25:
            return "gratitude" if coping >= 0.45 else "relief"
        return "joy" if certainty >= 0.55 else "hope"
    if agency < -0.25:
        return "anger" if certainty >= 0.60 else "fear"
    if agency > 0.25:
        return "shame" if norm < 0.45 else "guilt"
    return "distress" if coping < 0.40 else "sadness"


class Appraiser:
    """Turns an event into appraisal: novelty, relevance, control, RPE, threat.

    This is deliberately a heuristic. The calibration step later replaces
    text_affect() with ANEW / NRC-VAD / language-specific norms.
    """

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.expectation = 0.0
        self.recent: Deque[str] = deque(maxlen=24)

    def _novelty(self, text: str) -> float:
        if not text:
            return 0.3
        now = set(text)
        if not self.recent:
            return 0.55
        best = 0.0
        for old in self.recent:
            best = max(best, jaccard(now, set(old)))
        return clip(1.0 - best, 0.0, 1.0)

    def appraise(self, ev: Event, state=None, social=None) -> Appraisal:
        v, a = text_affect(ev.text)
        if ev.valence is not None:
            v = clip(ev.valence, -1.0, 1.0)
        if ev.arousal is not None:
            a = clip(ev.arousal, 0.0, 1.0)
        novelty = self._novelty(ev.text)
        relevance = 0.5 if ev.relevance is None else clip(ev.relevance, 0.0, 1.0)
        control = 0.5 if ev.control is None else clip(ev.control, 0.0, 1.0)
        social_sig = 0.0 if ev.social is None else clip(ev.social, -1.0, 1.0)
        rpe = clip(v - self.expectation, -2.0, 2.0)
        threat = clip(max(0.0, -v) * (1.0 - control), 0.0, 1.0)
        goal_relevance = relevance
        goal_congruence = clip(v, -1.0, 1.0)
        if ev.kind == "self_action" or ev.speaker in ("self", "me"):
            agency = 1.0
        elif ev.kind in ("tick", "observation"):
            agency = 0.0
        else:
            agency = -1.0
        certainty = clip(1.0 - 0.6 * novelty, 0.0, 1.0)
        willpower = 0.5
        energy = 0.5
        if state is not None:
            will = getattr(state, "will", None)
            willpower = float(getattr(will, "willpower", 0.5))
            drives = getattr(state, "drives", {})
            energy = float(getattr(drives, "energy", 0.5))
        coping = clip(0.5 * control + 0.3 * willpower + 0.2 * energy, 0.0, 1.0)
        norm = clip(1.0 - 0.5 * threat - 0.3 * max(0.0, -social_sig), 0.0, 1.0)
        self.expectation = 0.85 * self.expectation + 0.15 * v
        if ev.text:
            self.recent.append(ev.text)
        return Appraisal(
            novelty=novelty,
            relevance=relevance,
            control=control,
            rpe=rpe,
            social=social_sig,
            threat=threat,
            valence=v,
            arousal=a,
            goal_relevance=goal_relevance,
            goal_congruence=goal_congruence,
            agency=agency,
            certainty=certainty,
            coping_potential=coping,
            norm_compliance=norm,
            occ_emotion=occ_emotion(goal_congruence, certainty, agency, coping, norm),
        )
