from __future__ import annotations

import math
from typing import Dict

import numpy as np

EMOTIONS = ("joy", "sadness", "fear", "anger", "surprise", "trust", "loneliness")
# valence, arousal, dominance per emotion
DIMS = {
    "joy": (0.8, 0.5, 0.6),
    "sadness": (-0.7, 0.2, 0.2),
    "fear": (-0.8, 0.8, 0.2),
    "anger": (-0.6, 0.8, 0.7),
    "surprise": (0.0, 0.7, 0.4),
    "trust": (0.6, 0.3, 0.6),
    "loneliness": (-0.5, 0.3, 0.2),
}
TAU_HOURS = {"joy": 3.0, "sadness": 12.0, "fear": 8.0, "anger": 2.0,
             "surprise": 0.5, "trust": 24.0, "loneliness": 10.0}
APPRAISAL_DIMS = ("valence", "need", "fear", "joy", "agency", "certainty", "control", "novelty")
EXPRESSION_DIMS = ("length", "question", "diversity", "novelty", "confidence")
BIO_DIMS = ("valence", "arousal", "stress", "social", "safety", "energy", "anxiety")


class EmotionLayer:
    def __init__(self):
        self.intensity: Dict[str, float] = {k: 0.0 for k in EMOTIONS}
        self.confidence: float = 0.5
        self.rng = np.random.default_rng(1)
        self.W = self.rng.normal(0.0, 0.02, (len(EMOTIONS), 7))
        self.A = self.rng.normal(0.0, 0.10, (len(APPRAISAL_DIMS), len(EMOTIONS)))
        self.M = self.rng.normal(0.0, 0.08, (len(EXPRESSION_DIMS), len(EMOTIONS)))
        self.D = self.rng.normal(0.0, 0.10, (len(EMOTIONS), 3))
        self.B = self.rng.normal(0.0, 0.05, (len(BIO_DIMS), len(EMOTIONS)))
        self.log_tau = self.rng.normal(math.log(6 * 3600), 0.5, len(EMOTIONS))
        self.baseline = 0.0
        self.learn_lr = 0.01
        self.last_emo = np.zeros(len(EMOTIONS))
        self.last_action = None
        self.last_appraisal = np.zeros(len(APPRAISAL_DIMS))
        self.last_emotion_delta = np.zeros(len(EMOTIONS))
        self.last_expression = np.zeros(len(EXPRESSION_DIMS))
        self.last_expression_delta = np.zeros(len(EMOTIONS))
        self.last_bio = np.zeros(len(BIO_DIMS))
        self.last_bio_delta = np.zeros(len(EMOTIONS))

    def decay(self, hours: float) -> None:
        h = max(0.0, float(hours))
        for i, k in enumerate(EMOTIONS):
            tau = max(60.0, math.exp(float(self.log_tau[i])))
            self.intensity[k] *= math.exp(-h / tau)

    def appraise(self, app: Dict[str, object]) -> None:
        x = np.array([
            float(app.get("valence", 0.0) or 0.0),
            float(app.get("need_delta", 0.0) or 0.0),
            float(app.get("fear_delta", 0.0) or 0.0),
            float(app.get("joy_delta", 0.0) or 0.0),
            float(app.get("agency", 0.5) or 0.5) - 0.5,
            float(app.get("certainty", 0.5) or 0.5) - 0.5,
            float(app.get("control", 0.5) or 0.5) - 0.5,
            float(app.get("novelty", 0.0) or 0.0),
        ], dtype=float)
        delta = np.clip(self.A.T @ x, -1.0, 1.0)
        for i, k in enumerate(EMOTIONS):
            self.intensity[k] = max(0.0, min(1.0, self.intensity[k] + float(delta[i])))
        self.last_appraisal = x
        self.last_emotion_delta = delta
        self.confidence = max(0.1, min(1.0, 0.5 + 0.4 * float(np.linalg.norm(x)) / 2.0))

    def vector(self) -> Dict[str, float]:
        emo = np.array([self.intensity[k] for k in EMOTIONS], dtype=float)
        vec = self.D.T @ emo
        denom = max(0.3, float(emo.sum()))
        return {"valence": round(float(vec[0]) / denom, 4),
                "arousal": round(float(vec[1]) / denom, 4),
                "dominance": round(float(vec[2]) / denom, 4)}

    def self_express(self, metrics: Dict[str, float]) -> None:
        try:
            x = np.array([
                max(0.0, min(1.0, float(metrics.get("length", 0.0)) / 80.0)),
                max(0.0, min(1.0, float(metrics.get("question", 0.0)))),
                max(0.0, min(1.0, float(metrics.get("diversity", 0.0)))),
                max(0.0, min(1.0, float(metrics.get("novelty", 0.0)))),
                max(0.0, min(1.0, float(metrics.get("confidence", 0.0)))),
            ], dtype=float)
            delta = np.clip(self.M.T @ x, -1.0, 1.0)
            for i, k in enumerate(EMOTIONS):
                self.intensity[k] = max(0.0, min(1.0, self.intensity[k] + float(delta[i])))
            self.last_expression = x
            self.last_expression_delta = delta
        except Exception:
            pass

    def from_biology(self, mind) -> None:
        try:
            x = np.array([
                max(-1.0, min(1.0, float(mind.affect.v))),
                max(0.0, min(1.0, float(mind.affect.a))),
                max(0.0, min(1.0, float(mind.affect.stress))),
                max(0.0, min(1.0, float(mind.drives.values.get("social", 0.0)))),
                max(0.0, min(1.0, float(mind.drives.values.get("safety", 0.0)))),
                max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)))),
                max(0.0, min(1.0, float(getattr(getattr(mind, "attachment", None), "anxiety", 0.0)))),
            ], dtype=float)
            delta = np.clip(self.B.T @ x, -1.0, 1.0)
            for i, k in enumerate(EMOTIONS):
                self.intensity[k] = max(0.0, min(1.0, self.intensity[k] + 0.5 * float(delta[i])))
            self.last_bio = x
            self.last_bio_delta = delta
        except Exception:
            pass

    def apply_to(self, mind) -> None:
        try:
            emo = np.array([self.intensity[k] for k in EMOTIONS], dtype=float)
            action = self.W @ emo
            self.last_emo = emo
            self.last_action = action.copy()
            mind.affect.v = max(-1.0, min(1.0, float(mind.affect.v) + 0.10 * float(action[0])))
            mind.affect.a = max(0.0, min(1.0, float(mind.affect.a) + 0.10 * float(action[1])))
            mind.affect.stress = max(0.0, min(1.0, float(mind.affect.stress) + 0.10 * float(action[2])))
            mind.drives.values["social"] = max(0.0, min(1.0, float(mind.drives.values.get("social", 0.0)) + 0.10 * float(action[3])))
            mind.drives.values["safety"] = max(0.0, min(1.0, float(mind.drives.values.get("safety", 0.0)) + 0.10 * float(action[4])))
            mind.drives.values["energy"] = max(0.0, min(1.0, float(mind.drives.values.get("energy", 0.0)) + 0.10 * float(action[5])))
            if hasattr(mind, "attachment"):
                mind.attachment.anxiety = max(0.0, min(1.0, float(getattr(mind.attachment, "anxiety", 0.0)) + 0.10 * float(action[6])))
        except Exception:
            pass

    def nudge(self, deltas: dict) -> None:
        try:
            for k, dv in dict(deltas or {}).items():
                if k in self.intensity:
                    self.intensity[k] = max(0.0, min(1.0, float(self.intensity[k]) + float(dv)))
        except Exception:
            pass

    def learn(self, reward: float) -> None:
        try:
            raw = float(reward)
            self.baseline = self.baseline + 0.05 * (raw - self.baseline)
            r = max(-1.0, min(1.0, raw - self.baseline))
            if self.last_action is not None:
                self.W = np.clip(self.W + self.learn_lr * r * np.outer(self.last_action, self.last_emo), -1.0, 1.0)
            if np.any(self.last_appraisal):
                self.A = np.clip(self.A + self.learn_lr * r * np.outer(self.last_appraisal, self.last_emotion_delta), -1.0, 1.0)
            if np.any(self.last_expression):
                self.M = np.clip(self.M + self.learn_lr * r * np.outer(self.last_expression, self.last_expression_delta), -1.0, 1.0)
            if np.any(self.last_bio):
                self.B = np.clip(self.B + self.learn_lr * r * np.outer(self.last_bio, self.last_bio_delta), -1.0, 1.0)
            if self.last_emo.sum() > 0:
                vec = self.D.T @ self.last_emo
                self.D = np.clip(self.D + self.learn_lr * r * np.outer(self.last_emo, vec / max(0.3, self.last_emo.sum())), -1.0, 1.0)
                self.log_tau = np.clip(self.log_tau + 0.01 * r * self.last_emo, math.log(300.0), math.log(7 * 86400.0))
        except Exception:
            pass

    def prompt_text(self) -> str:
        if sum(self.intensity.values()) < 0.05:
            return ""
        vals = ",".join("%s=%.3f" % (k, self.intensity[k]) for k in EMOTIONS)
        vec = self.vector()
        return "emotion_intensity=[%s] emotion_vector valence=%.3f arousal=%.3f dominance=%.3f" % (
            vals, vec["valence"], vec["arousal"], vec["dominance"])

    def snapshot(self) -> Dict[str, object]:
        return {"intensity": {k: round(v, 4) for k, v in self.intensity.items()},
                "vector": self.vector(), "confidence": round(self.confidence, 3),
                "W": [[round(float(x), 4) for x in row] for row in self.W],
                "A": [[round(float(x), 4) for x in row] for row in self.A],
                "M": [[round(float(x), 4) for x in row] for row in self.M],
                "D": [[round(float(x), 4) for x in row] for row in self.D],
                "B": [[round(float(x), 4) for x in row] for row in self.B],
                "log_tau": [round(float(x), 4) for x in self.log_tau],
                "learn_baseline": round(float(self.baseline), 4)}

    def load(self, data: Dict[str, object]) -> None:
        try:
            intensity = data.get("intensity") or {}
            for k in EMOTIONS:
                self.intensity[k] = max(0.0, min(1.0, float(intensity.get(k, 0.0))))
            self.confidence = float(data.get("confidence", 0.5))
            if data.get("W"):
                self.W = np.array(data.get("W"), dtype=float)
            if data.get("A"):
                self.A = np.array(data.get("A"), dtype=float)
            if data.get("M"):
                self.M = np.array(data.get("M"), dtype=float)
            if data.get("D"):
                self.D = np.array(data.get("D"), dtype=float)
            if data.get("B"):
                self.B = np.array(data.get("B"), dtype=float)
            if data.get("log_tau"):
                self.log_tau = np.array(data.get("log_tau"), dtype=float)
            self.baseline = float(data.get("learn_baseline", 0.0))
        except Exception:
            pass
