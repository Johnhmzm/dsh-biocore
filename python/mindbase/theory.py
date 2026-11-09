from __future__ import annotations

from typing import Dict

from .active_inference import ActiveInference


def prediction_error(value: float, expected: float) -> float:
    return float(value) - float(expected)


def allostatic_error(value: float, setpoint: float) -> float:
    return float(value) - float(setpoint)


def rpe_update(value: float, reward: float, prediction: float, lr: float) -> float:
    return float(value) + lr * (float(reward) - float(prediction))


def unified_state(mb) -> Dict[str, object]:
    """One state vector shared by biological, mathematical, digital and psychological layers."""
    drives = mb.drives
    self_model = mb.self_model
    load = drives.allostatic_load()
    ai = (getattr(mb, 'active_inference', None) or ActiveInference()).choose(load, abs(self_model.prediction_error), drives.get("social"), drives.get("energy"))
    last = mb.learner.log[-1] if mb.learner.log else {}
    return {
        "E": round(load, 4),
        "RPE": round(float(last.get("rpe", 0.0) or 0.0), 4),
        "F": min(float(ai["efe_speak"]), float(ai["efe_silence"])),
        "S": round(float(self_model.prediction_error), 4),
        "A": {
            "valence": round(mb.affect.v, 3),
            "arousal": round(mb.affect.a, 3),
            "stress": round(mb.affect.stress, 3),
            "mood": round(mb.affect.mood, 3),
        },
        "drives": drives.snapshot(),
        "self": self_model.snapshot(),
        "memory": mb.memory.snapshot(),
        "active_inference": ai,
    }
