"""Bidirectional translation contract.

LLM -> bio: text/event -> numeric appraisal vector.
bio -> LLM: state -> compact intent tags.
The translator never generates language and never makes final decisions.
"""
from __future__ import annotations

from .bio_semantic import translate


def _f(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return float(default)


class TranslationLayer:
    def __init__(self):
        self.last_inbound = {}
        self.last_outbound = {}
        self.last_feedback = {}

    def inbound(self, text, sensor=None, confidence=None):
        s = dict(sensor or {})
        event = {
            "valence": _f(s.get("valence"), 0.0),
            "arousal": _f(s.get("arousal"), 0.5),
            "dominance": _f(s.get("dominance"), 0.0),
            "intensity": _f(s.get("intensity"), 0.5),
            "threat": _f(s.get("threat"), 0.0),
            "stress": _f(s.get("stress"), 0.0),
            "guilt": _f(s.get("guilt"), 0.0),
            "shame": _f(s.get("shame"), 0.0),
            "connection_delta": _f(s.get("connection_delta"), 0.0),
            "confidence": _f(confidence if confidence is not None else s.get("confidence"), 0.5),
            "source": "llm" if s else "local",
        }
        self.last_inbound = event
        return event

    def outbound(self, mind, behavior=None, ripple_outputs=None, trust=None, emotion=None, event=None):
        intent = translate(mind, behavior, ripple_outputs, trust=trust, emotion=emotion, event=event)
        self.last_outbound = intent
        return intent

    def apply_event(self, mind, event):
        e = dict(event or {})
        try:
            mind.inject_affect(e.get("valence", 0.0), e.get("arousal", 0.5),
                               e.get("dominance", 0.0),
                               intensity=max(0.1, _f(e.get("intensity"), 0.5)),
                               blend=0.5)
        except Exception:
            pass
        try:
            v = _f(e.get("valence"), 0.0)
            stress = max(0.0, -v) * 0.5 + _f(e.get("threat"), 0.0) * 0.5 + _f(e.get("stress"), 0.0) * 0.5
            mind.affect.stress = max(0.0, min(1.0, _f(getattr(mind.affect, "stress", 0.2)) + 0.10 * stress))
        except Exception:
            pass

    def feedback(self, mind, reply, user_text=""):
        r = str(reply or "")
        t = str(user_text or "")
        warm = sum(1 for w in ("谢谢", "你", "在", "陪", "懂", "暖") if w in r)
        cold = sum(1 for w in ("不想", "别", "烦", "算了", "随便") if w in r)
        delta = 0.01 * warm - 0.01 * cold
        try:
            mind.social.trust_for  # noqa
        except Exception:
            pass
        try:
            mind.affect.stress = max(0.0, min(1.0, _f(getattr(mind.affect, "stress", 0.2))
                                             + 0.02 * cold - 0.025))
        except Exception:
            pass
        self.last_feedback = {"trust_delta": round(delta, 4), "warm": warm, "cold": cold}
        return dict(self.last_feedback)
