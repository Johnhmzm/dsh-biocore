"""Paper-grounded emotion core.

This is the numeric "inner" layer: appraisal -> core affect -> stress ->
action tendency -> expression factors.  The LLM is treated as a noisy sensor
(observation + confidence), never as the emotion itself.

References (constructs, not prompt text):
- Scherer (2009) CPM: relevance, implication, coping, norm.
- Russell (1980) circumplex; Mehrabian & Russell PAD.
- Seth (2013) interoceptive inference.
- Sterling (2012) allostasis; McEwen (1998) allostatic load.
- Gross (1998) emotion regulation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


@dataclass
class AppraisalObservation:
    relevance: float = 0.5
    goal_congruence: float = 0.0
    coping: float = 0.5
    norm_compatibility: float = 0.5
    agency: float = 0.5
    certainty: float = 0.5
    novelty: float = 0.2
    confidence: float = 0.5

    def clamped(self) -> "AppraisalObservation":
        return AppraisalObservation(
            relevance=_clip(self.relevance), goal_congruence=max(-1.0, min(1.0, float(self.goal_congruence))),
            coping=_clip(self.coping), norm_compatibility=_clip(self.norm_compatibility),
            agency=_clip(self.agency), certainty=_clip(self.certainty),
            novelty=_clip(self.novelty), confidence=_clip(self.confidence))


@dataclass
class EmotionCore:
    valence: float = 0.0
    arousal: float = 0.3
    dominance: float = 0.5
    stress: float = 0.2
    mood: float = 0.0
    action_tendency: dict = field(default_factory=lambda: {
        "approach": 0.25, "avoid": 0.25, "repair": 0.15,
        "seek_support": 0.15, "freeze": 0.10, "withdraw": 0.10})
    last: dict = field(default_factory=dict)

    def observe(self, app: AppraisalObservation, body: dict | None = None,
                trust: float = 0.5, relationship: str = "",
                memory_valence: float = 0.0) -> dict:
        a = app.clamped()
        c = _clip(a.confidence)
        b = body or {}
        energy = _clip(b.get("energy", 0.7))
        fatigue = _clip(b.get("fatigue", 0.2))
        hydration = _clip(b.get("hydration", 0.6))

        target_v = 0.8 * a.goal_congruence * a.relevance + 0.2 * memory_valence
        self.valence = _clip(self.valence + 0.25 * c * (target_v - self.valence)
                             - 0.02 * self.valence, -1.0, 1.0)
        target_a = 0.35 * a.relevance + 0.25 * a.novelty + 0.25 * abs(a.goal_congruence)
        self.arousal = _clip(self.arousal + 0.20 * c * (target_a - self.arousal))
        target_d = 0.45 + 0.25 * a.coping + 0.20 * a.agency - 0.25 * max(0.0, -self.valence)
        self.dominance = _clip(self.dominance + 0.15 * c * (target_d - self.dominance))

        threat = a.relevance * (max(0.0, -a.goal_congruence) * 0.7 +
                                (1.0 - a.coping) * 0.3)
        self.stress = _clip(self.stress + 0.12 * c * threat
                            - 0.03 * (a.coping + trust) * 0.5)
        self.mood = _clip(self.mood + 0.02 * (self.valence - self.mood), -1.0, 1.0)

        # action tendencies: softmax-like normalized utilities
        u = {
            "approach": 0.3 + 0.4 * max(0.0, self.valence) + 0.2 * a.coping,
            "avoid": 0.2 + 0.5 * max(0.0, -self.valence) + 0.2 * a.novelty,
            "repair": 0.2 + 0.4 * (1.0 - a.norm_compatibility) + 0.2 * a.relevance,
            "seek_support": 0.2 + 0.3 * (1.0 - self.dominance) + 0.3 * a.coping,
            "freeze": 0.1 + 0.4 * self.arousal * (1.0 - a.coping),
            "withdraw": 0.1 + 0.4 * fatigue + 0.2 * (1.0 - energy),
        }
        total = sum(max(0.0, v) for v in u.values()) or 1.0
        self.action_tendency = {k: round(max(0.0, v) / total, 4) for k, v in u.items()}
        self.last = {
            "valence": round(self.valence, 4), "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4), "stress": round(self.stress, 4),
            "mood": round(self.mood, 4), "confidence": round(c, 4),
            "memory_valence": round(max(-1.0, min(1.0, float(memory_valence))), 4),
            "action": max(self.action_tendency, key=self.action_tendency.get),
            "hydration": round(hydration, 3),
        }
        return self.last

    def factors(self) -> dict:
        return dict(self.last or {
            "valence": round(self.valence, 4), "arousal": round(self.arousal, 4),
            "dominance": round(self.dominance, 4), "stress": round(self.stress, 4),
            "mood": round(self.mood, 4), "action": "approach"})

    def context(self) -> str:
        f = self.factors()
        return ("情绪核心：valence=%.2f arousal=%.2f dominance=%.2f stress=%.2f "
                "mood=%.2f 行动倾向=%s；这是身体/关系积分后的状态，不要直接报数值。"
                % (f.get("valence", 0.0), f.get("arousal", 0.0), f.get("dominance", 0.0),
                   f.get("stress", 0.0), f.get("mood", 0.0), f.get("action", "approach")))
