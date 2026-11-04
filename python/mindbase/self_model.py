from __future__ import annotations

import random
from typing import Dict

from .noise import clip, ou
from .types import Appraisal, Event


class SelfModel:
    """Minimal self: agency, continuity, consistency, identity.

    identity is a slow variable: contradictions move it down, coherent
    experience lets it recover. This is operational, not a consciousness claim.
    """

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.agency = 0.50
        self.continuity = 0.50
        self.consistency = 0.80
        self.identity = 0.70
        self.value_stability = 1.0
        self.feedback_expectation = 0.0
        self.identity_prediction = self.identity
        self.identity_setpoint_base = 0.70
        self.prediction_error = 0.0
        # allostatic self-efficacy: can I reduce the current setpoint error?
        self.allostatic_self_efficacy = 0.50
        self.expression_confidence = 0.50
        self.identity_gain = 1.0

    def on_event(self, ev: Event, ap: Appraisal) -> None:
        if ev.speaker == "self":
            self.agency = clip(self.agency + 0.03, 0.0, 1.0)
            self.continuity = clip(self.continuity + 0.01, 0.0, 1.0)
        else:
            self.agency = clip(self.agency - 0.005, 0.0, 1.0)
            self.continuity = clip(self.continuity + 0.002, 0.0, 1.0)
        self.consistency = clip(
            0.95 * self.consistency + 0.05 * (1.0 - abs(ap.valence) * 0.2),
            0.0,
            1.0,
        )

    def on_action(self, action: str, spoke: bool) -> None:
        target = 1.0 if spoke else 0.35
        self.agency = ou(self.agency, target, 1.0 / 60.0, 0.05, 1.0, self.rng)
        self.continuity = clip(self.continuity + (0.002 if spoke else 0.0), 0.0, 1.0)
        self.identity = clip(self.identity + (0.001 if spoke else -0.0005) * float(getattr(self, "identity_gain", 1.0)), 0.0, 1.0)

    def tick(self, dt: float) -> None:
        rate = min(1.0, max(0.0, dt) / (7.0 * 86400.0))
        # value stability recovers when contradictions stop arriving
        self.value_stability = clip(
            self.value_stability + (1.0 - self.value_stability) * rate, 0.0, 1.0
        )
        target = clip(
            0.50 * self.continuity + 0.30 * self.consistency + 0.20 * self.value_stability,
            0.0,
            1.0,
        )
        self.identity = clip(self.identity + (target - self.identity) * rate, 0.0, 1.0)
        # predictive self: predict before observing, then update from error
        pred_err = self.identity - self.identity_prediction
        self.identity_prediction = clip(self.identity_prediction + 0.20 * pred_err, 0.0, 1.0)
        self.prediction_error = pred_err

    def on_expression(self, metrics: Dict[str, float]) -> None:
        length = float(metrics.get("length", 0.0))
        question = float(metrics.get("question", 0.0))
        diversity = float(metrics.get("diversity", 0.0))
        novelty = float(metrics.get("novelty", 0.0))
        quality = clip(0.30 * min(1.0, length / 80.0) + 0.25 * diversity + 0.30 * novelty + 0.15 * question, 0.0, 1.0)
        self.expression_confidence = clip(0.90 * self.expression_confidence + 0.10 * quality, 0.0, 1.0)
        self.identity = clip(self.identity + 0.002 * (quality - 0.50) * float(getattr(self, "identity_gain", 1.0)), 0.0, 1.0)

    def on_feedback(self, value_delta: Dict[str, float], conflict: str = "none", signal: float = 0.0) -> None:
        # Unified RPE: fly DAN and human dopamine both encode reward prediction error.
        sig = clip(float(signal), -1.0, 1.0)
        rpe = sig - self.feedback_expectation
        self.feedback_expectation = clip(self.feedback_expectation + 0.20 * rpe, -1.0, 1.0)
        # value/identity are slow integrators of the same prediction error
        self.value_stability = clip(self.value_stability + 0.06 * rpe, 0.0, 1.0)
        # self-verification: identity is an attractor around a feedback-dependent setpoint
        self.identity_setpoint = clip(float(getattr(self, "identity_setpoint_base", 0.70)) + 0.25 * self.feedback_expectation, 0.30, 0.92)
        gain = float(getattr(self, "identity_gain", 1.0))
        self.identity = clip(self.identity + gain * (0.05 * (self.identity_setpoint - self.identity) + 0.02 * rpe), 0.0, 1.0)
        # goal conflict is an additional aversive prediction error, not a hand-tuned valence
        if conflict == "high":
            self.identity = clip(self.identity - 0.01, 0.0, 1.0)

    def update_allostatic_efficacy(self, load: float, confidence: float, anxiety: float = 0.0, social_load: float = 0.0, physiological_load: float = 0.0, safety_load: float = 0.0, social_support: float = 0.0) -> None:
        ax = clip(float(anxiety), 0.0, 1.0)
        # social baseline: social load weighs more under anxious attachment
        social_weight = (0.40 + 0.40 * ax) * (1.0 - 0.30 * clip(float(social_support), 0.0, 1.0))
        weighted = 0.40 * float(physiological_load) + social_weight * float(social_load) + 0.40 * float(safety_load)
        load = clip(max(float(load), weighted), 0.0, 1.0)
        conf = clip(float(confidence) * (1.0 - 0.30 * ax), 0.0, 1.0)
        self.ase_components = {"load": round(load, 4), "confidence": round(conf, 4), "anxiety": round(clip(float(anxiety), 0.0, 1.0), 4)}
        self.allostatic_self_efficacy = clip(conf * (1.0 - load), 0.0, 1.0)

    def snapshot(self) -> Dict[str, float]:
        return {
            "agency": round(self.agency, 3),
            "continuity": round(self.continuity, 3),
            "consistency": round(self.consistency, 3),
            "identity": round(self.identity, 3),
            "value_stability": round(self.value_stability, 3),
            "feedback_expectation": round(self.feedback_expectation, 3),
            "identity_setpoint": round(getattr(self, "identity_setpoint", 0.70), 3),
            "identity_prediction": round(self.identity_prediction, 3),
            "prediction_error": round(self.prediction_error, 4),
            "allostatic_self_efficacy": round(self.allostatic_self_efficacy, 3),
            "ase_components": dict(getattr(self, "ase_components", {})),
        }
