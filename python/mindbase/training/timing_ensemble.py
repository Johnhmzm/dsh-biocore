"""Multi-solution timing ensemble.

Components:
  - supervised TimingModel probability
  - survival hazard response probability
  - a weak prior from the base (directed question / constant)

Weights are fit on validation data, not hand-written.  This is a small stacking
layer, not a new monolithic model.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .survival import DiscreteTimeHazard
from .timing import TimingModel


class TimingEnsemble:
    def __init__(self, timing: TimingModel, hazard: DiscreteTimeHazard):
        self.timing = timing
        self.hazard = hazard
        self.weights = [0.5, 0.3, 0.2]  # timing, hazard, prior

    def _prior(self, context: str) -> float:
        text = str(context or "")
        if "?" in text or "？" in text or "在吗" in text or "你好" in text:
            return 0.80
        return 0.60

    def component_probs(self, context: str, state=None) -> Tuple[float, float, float]:
        p_t = float(self.timing.predict_proba(context, state).get("speak", 0.0))
        try:
            p_h = float(self.hazard.predict_response_by(context, state)[0])
        except Exception:
            p_h = 0.5
        return p_t, p_h, self._prior(context)

    def predict_proba(self, context: str, state=None) -> Dict[str, float]:
        pt, ph, pp = self.component_probs(context, state)
        w = self.weights
        s = max(1e-9, sum(w))
        p = (w[0] * pt + w[1] * ph + w[2] * pp) / s
        p = max(0.0, min(1.0, p))
        return {"speak": round(p, 4), "silence": round(1.0 - p, 4)}

    def policy_hint(self, context: str, state=None) -> Tuple[str, float]:
        p = self.predict_proba(context, state)
        if p["speak"] >= p["silence"]:
            return "speak", p["speak"]
        return "silence", p["silence"]

    def fit_weights(self, records: Sequence[Sequence[object]]) -> List[float]:
        rows = []
        for item in records:
            ctx = str(item[0])
            y = int(item[1])
            state = item[2] if len(item) > 2 else None
            rows.append((ctx, y, state))
        best_w, best_score = [0.5, 0.3, 0.2], -1e9
        for i in range(0, 11):
            wt = i / 10.0
            for j in range(0, 11 - i):
                wh = j / 10.0
                wp = 1.0 - wt - wh
                total = 0.0
                for ctx, y, state in rows:
                    pt, ph, pp = self.component_probs(ctx, state)
                    p = wt * pt + wh * ph + wp * pp
                    # Brier score, lower is better
                    total -= (p - y) ** 2
                    # majority-class robust term
                    if (p >= 0.5) == bool(y):
                        total += 0.05
                if total > best_score:
                    best_score = total
                    best_w = [round(wt, 3), round(wh, 3), round(wp, 3)]
        self.weights = best_w
        return best_w
