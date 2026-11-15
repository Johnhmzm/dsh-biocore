"""Finite-horizon optimal stopping for speak/silence timing.

Math transfer: timing is not only classification; it is an optimal stopping
problem.  Given response probability p_t, benefit b, speaking cost c_s and
waiting cost c_w, the Bellman recursion is:

  V_speak = b * p_t - c_s
  V_wait  = -c_w + gamma * V_{t+1}
  V_t     = max(V_speak, V_wait)

The resulting policy is a threshold in p_t; no hand-written rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


@dataclass
class StoppingParams:
    benefit: float = 1.0
    speak_cost: float = 0.20
    wait_cost: float = 0.02
    gamma: float = 0.95


def value_iteration(probabilities: Sequence[float], params: StoppingParams | None = None) -> Dict[str, object]:
    p = StoppingParams() if params is None else params
    probs = [max(0.0, min(1.0, float(x))) for x in probabilities]
    n = len(probs)
    values = [0.0] * (n + 1)
    actions: List[str] = ["wait"] * n
    speak_values = [0.0] * n
    wait_values = [0.0] * n
    for t in range(n - 1, -1, -1):
        speak_values[t] = p.benefit * probs[t] - p.speak_cost
        wait_values[t] = -p.wait_cost + p.gamma * values[t + 1]
        if speak_values[t] >= wait_values[t]:
            values[t] = speak_values[t]
            actions[t] = "speak"
        else:
            values[t] = wait_values[t]
            actions[t] = "wait"
    return {
        "value": round(values[0], 6),
        "values": [round(v, 6) for v in values[:-1]],
        "actions": actions,
        "speak_values": [round(v, 6) for v in speak_values],
        "wait_values": [round(v, 6) for v in wait_values],
    }


def threshold(probabilities: Sequence[float], params: StoppingParams | None = None) -> float:
    """Smallest p at which speaking is optimal (linear interpolation is not needed)."""
    p = StoppingParams() if params is None else params
    results = value_iteration(probabilities, p)
    for prob, action in zip(probabilities, results["actions"]):
        if action == "speak":
            return float(prob)
    return 1.01


def decide(probability: float, params: StoppingParams | None = None, continuation_value: float = 0.0) -> str:
    p = StoppingParams() if params is None else params
    speak = p.benefit * float(probability) - p.speak_cost
    wait = -p.wait_cost + p.gamma * float(continuation_value)
    return "speak" if speak >= wait else "wait"

