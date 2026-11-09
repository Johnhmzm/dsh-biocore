from __future__ import annotations

from typing import Dict


def expected_free_energy(load: float, pred_err: float, social: float, energy: float, action: str, activity: float = 0.0) -> float:
    """Expected free energy for a binary action (speak/silence).

    risk     = expected allostatic deviation after action
    ambiguity= self-prediction uncertainty
    cost     = action cost
    """
    if action == "speak":
        risk = max(0.0, load - 0.10 * social)
        cost = 0.05 + 0.05 * max(0.0, 0.3 - energy) + 0.10 * max(0.0, float(activity))
    else:
        risk = load + 0.08 * social
        cost = 0.02
    ambiguity = pred_err * pred_err
    return risk + 0.5 * ambiguity + cost


def choose(load: float, pred_err: float, social: float, energy: float) -> Dict[str, object]:
    speak = expected_free_energy(load, pred_err, social, energy, "speak")
    silence = expected_free_energy(load, pred_err, social, energy, "silence")
    action = "speak" if speak < silence else "silence"
    return {"action": action, "efe_speak": round(speak, 4), "efe_silence": round(silence, 4)}


class ActiveInference:
    def __init__(self):
        self.w_risk = 1.0
        self.w_amb = 0.5
        self.w_cost = 1.0
        self.lr = 0.005

    def choose(self, load: float, pred_err: float, social: float, energy: float, activity: float = 0.0, valence: float = 0.0):
        def efe(action: str) -> float:
            if action == "speak":
                risk = max(0.0, load - 0.10 * social - 0.15 * float(valence))
                cost = 0.05 + 0.05 * max(0.0, 0.3 - energy) + 0.10 * max(0.0, float(activity))
            else:
                risk = load + 0.08 * social + 0.10 * float(valence)
                cost = 0.02
            return self.w_risk * risk + self.w_amb * pred_err * pred_err + self.w_cost * cost
        s, q = efe("speak"), efe("silence")
        return {"action": "speak" if s < q else "silence", "efe_speak": round(s, 4), "efe_silence": round(q, 4)}

    def learn(self, rpe: float, load: float, pred_err: float) -> None:
        self.w_risk = max(0.1, min(3.0, self.w_risk - self.lr * float(rpe) * float(load)))
        self.w_amb = max(0.05, min(3.0, self.w_amb - self.lr * float(rpe) * float(pred_err) ** 2))

    def snapshot(self) -> dict:
        return {"w_risk": round(self.w_risk, 4), "w_amb": round(self.w_amb, 4), "w_cost": self.w_cost}
