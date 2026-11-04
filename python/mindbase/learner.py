from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

from .config import MindConfig
from .mb import MushroomBody
from .noise import clip


@dataclass
class Experience:
    cue: str
    feedback: float
    prediction: float
    rpe: float
    t: float
    visits: int = 0

    def priority(self) -> float:
        # surprising and rare experiences get replayed first
        rarity = 1.0 / (1.0 + self.visits)
        return abs(self.rpe) * (1.0 + rarity)


class Learner:
    """Digital-fly learning loop with admission + curriculum replay.

    - events leave an eligibility trace (delay credit assignment)
    - only explicit, informative feedback is admitted
    - admitted experiences go to a replay buffer, ranked by surprise/rarity
    - consolidation replays top-ranked experiences, then homeostatic scaling
    """

    def __init__(
        self,
        cfg: MindConfig,
        mb: MushroomBody,
        rng: random.Random,
        min_feedback: float = 0.05,
        min_rpe: float = 0.005,
    ):
        self.cfg = cfg
        self.mb = mb
        self.rng = rng
        self.min_feedback = min_feedback
        self.min_rpe = min_rpe
        self.trace: Deque[Tuple[str, float, float]] = deque(maxlen=16)
        self.replay: Deque[Experience] = deque(maxlen=128)
        self.intrinsic_replay: Deque[Experience] = deque(maxlen=64)
        self.log = deque(maxlen=256)
        self.counts: Dict[str, int] = {}
        self.admitted = 0
        self.rejected = 0
        self.forgotten = 0
        self.reinforcement_scale = 1.0
        self.use_trace = True
        # intrinsic consolidation gain is learned from co-occurring extrinsic feedback
        self.intrinsic_gain = 0.25
        self.intrinsic_gain_lr = 0.01
        self.dan_reward_lr = 0.30
        self.dan_punishment_lr = 0.30

    def _decay_trace(self) -> None:
        self.trace = deque(((text, elig * 0.70, ts) for text, elig, ts in self.trace), maxlen=16)

    def on_event(self, text: str, valence: float, now: float = 0.0) -> None:
        self._decay_trace()
        if text:
            self.trace.append((text, 1.0, now))
        self.mb.hab.on_cue(text, valence, self.cfg.dt)

    def admit(self, cue: str, feedback: float, prediction: float) -> Tuple[bool, str, float]:
        """Decide whether an experience is worth learning from."""
        fb = clip(feedback, -1.0, 1.0)
        if abs(fb) < self.min_feedback:
            return False, "low_signal", 0.0
        rpe = fb - prediction
        visits = self.counts.get(cue, 0)
        self.counts[cue] = visits + 1
        if visits > 0 and abs(rpe) < self.min_rpe:
            return False, "redundant", rpe
        return True, "admitted", rpe

    def on_reply(self, text: str, feedback: Optional[float] = None, now: float = 0.0, intrinsic: float = 0.0, dan: float = 0.0, dan_reward: Optional[float] = None, dan_punishment: Optional[float] = None, region_gain: float = 1.0) -> Dict[str, object]:
        pred, _ = self.mb.predict(text)
        if abs(float(intrinsic)) >= 0.02:
            self.intrinsic_replay.append(Experience(cue=text, feedback=clip(float(intrinsic), -1.0, 1.0), prediction=pred, rpe=float(intrinsic) - pred, t=now))
            if feedback is not None and abs(float(feedback)) >= 0.05:
                self.intrinsic_gain = clip(
                    self.intrinsic_gain + self.intrinsic_gain_lr * float(feedback) * max(0.0, -float(intrinsic)),
                    0.0, 0.80,
                )
        if feedback is None or abs(feedback) < 1e-9:
            hab = self.mb.hab.on_cue(text, 0.0, self.cfg.dt)
            out: Dict[str, object] = {
                "prediction": pred,
                "dan": 0.0,
                "habituation": hab,
                "learned": False,
                "admitted": False,
                "reason": "no_feedback",
                "credited": 0,
                "credits": {},
            }
        else:
            fb = clip(feedback, -1.0, 1.0)
            ok, reason, rpe = self.admit(text, fb, pred)
            if not ok:
                self.rejected += 1
                out = {
                    "prediction": pred,
                    "dan": 0.0,
                    "habituation": self.mb.hab.on_cue(text, 0.0, self.cfg.dt),
                    "learned": False,
                    "admitted": False,
                    "reason": reason,
                    "credited": 0,
                    "credits": {},
                }
            else:
                self.admitted += 1
                if dan_reward is not None and dan_punishment is not None:
                    gate_dan = float(dan_reward) if fb > 0 else float(dan_punishment)
                else:
                    gate_dan = float(dan)
                gate_lr = self.dan_reward_lr if fb > 0 else self.dan_punishment_lr
                fb_eff = clip(fb * self.reinforcement_scale * (1.0 + gate_lr * gate_dan) * float(region_gain), -1.0, 1.0)
                if fb > 0:
                    self.dan_reward_lr = clip(self.dan_reward_lr + 0.002 * (abs(fb) - 0.5), 0.05, 0.80)
                else:
                    self.dan_punishment_lr = clip(self.dan_punishment_lr + 0.002 * (abs(fb) - 0.5), 0.05, 0.80)
                out = self.mb.learn(text, fb_eff, self.cfg.dt)
                out["learned"] = True
                out["admitted"] = True
                out["reason"] = reason
                credits: Dict[str, float] = {}
                for cue, elig, _ts in (list(self.trace) if self.use_trace else []):
                    if cue == text:
                        continue
                    credit = fb_eff * 0.20 * elig
                    if abs(credit) >= 0.01:
                        self.mb.learn(cue, credit, self.cfg.dt)
                        credits[cue] = round(credit, 5)
                out["credits"] = credits
                out["credited"] = len(credits)
                self.replay.append(
                    Experience(cue=text, feedback=fb_eff, prediction=pred, rpe=rpe, t=now)
                )
        self.log.append(out)
        return out

    def consolidate(self) -> None:
        self.mb.consolidate()
        ranked = sorted(self.replay, key=lambda e: e.priority(), reverse=True)[:32]
        keep = []
        for exp in self.replay:
            # active forgetting: low-value, repeatedly seen traces decay away
            if exp.visits >= 2 and abs(exp.feedback) < 0.20:
                self.forgotten += 1
                continue
            keep.append(exp)
        self.replay = deque(keep, maxlen=128)
        for exp in ranked:
            if exp in self.replay and abs(exp.feedback) >= 0.20:
                self.mb.learn(exp.cue, exp.feedback * 0.5, self.cfg.dt)
                exp.visits += 1
        # homeostatic channel consolidates separately, at lower gain
        ranked_intrinsic = sorted(self.intrinsic_replay, key=lambda e: abs(e.feedback), reverse=True)[:16]
        for exp in ranked_intrinsic:
            if abs(exp.feedback) >= 0.05:
                self.mb.learn(exp.cue, exp.feedback * self.intrinsic_gain, self.cfg.dt)
                exp.visits += 1

    def snapshot(self) -> Dict[str, object]:
        return {
            "replay": len(self.replay),
            "intrinsic_replay": len(self.intrinsic_replay),
            "intrinsic_gain": round(self.intrinsic_gain, 4),
            "dan_reward_lr": round(self.dan_reward_lr, 4),
            "dan_punishment_lr": round(self.dan_punishment_lr, 4),
            "log": len(self.log),
            "admitted": self.admitted,
            "rejected": self.rejected,
            "forgotten": self.forgotten,
            "trace": [
                {"text": t, "eligibility": round(e, 3), "t": round(ts, 1)}
                for t, e, ts in list(self.trace)[-5:]
            ],
            "top_replay": [
                {"cue": e.cue, "feedback": round(e.feedback, 3), "priority": round(e.priority(), 3)}
                for e in sorted(self.replay, key=lambda e: e.priority(), reverse=True)[:5]
            ],
        }
