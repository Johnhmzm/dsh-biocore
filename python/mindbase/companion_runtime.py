"""Persistent companion runtime: heartbeat, memory books, affect, learning.

The model is only one tool; the character lives in the loop outside it.
Borrow the housecat runtime shell, but replace its rules with biological state.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .affective import perceive_emotion_lexicon
from .noise import clip

ROOT = Path("mindbase/data/companion_runtime")


class MemoryStore:
    """Diary / impressions / profiles / learning / blacklist / audit."""

    def __init__(self, root: Path | str = ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("diary", "impressions", "profiles", "learning", "blacklist"):
            path = self.root / f"{name}.json"
            if not path.exists():
                path.write_text(json.dumps([] if name == "diary" else {}, ensure_ascii=False), encoding="utf-8")

    def _load(self, name: str):
        try:
            return json.loads((self.root / f"{name}.json").read_text(encoding="utf-8"))
        except Exception:
            return [] if name == "diary" else {}

    def _save(self, name: str, data) -> None:
        (self.root / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def append_diary(self, text: str, tags: Sequence[str] = ()) -> None:
        rows = self._load("diary")
        rows.append({"t": time.time(), "text": str(text), "tags": list(tags)})
        self._save("diary", rows[-500:])

    def upsert_impression(self, user_id: str, text: str) -> None:
        data = self._load("impressions")
        data[str(user_id)] = {"text": str(text)[:1200], "t": time.time()}
        self._save("impressions", data)

    def upsert_profile(self, user_id: str, **fields) -> None:
        data = self._load("profiles")
        data.setdefault(str(user_id), {}).update(fields)
        self._save("profiles", data)

    def learn(self, text: str, reward: Optional[float] = None) -> None:
        data = self._load("learning")
        data[str(int(time.time() * 1000))] = {"text": str(text)[:400], "reward": reward}
        self._save("learning", data)

    def blacklist(self, text: str) -> None:
        data = self._load("blacklist")
        data[str(int(time.time() * 1000))] = str(text)[:400]
        self._save("blacklist", data)

    def recall(self, user_id: str, limit: int = 5) -> str:
        parts: List[str] = []
        learning = self._load("learning")
        if learning:
            rows = list(learning.values())[-3:]
            texts = [str(r.get("text", r)) if isinstance(r, dict) else str(r) for r in rows]
            parts.append("她学到的：" + " / ".join(texts))
        imp = (self._load("impressions") or {}).get(str(user_id))
        if imp:
            parts.append("她对这个人的印象：" + str(imp.get("text", ""))[:300])
        prof = (self._load("profiles") or {}).get(str(user_id))
        if prof:
            parts.append("这个人的资料：" + json.dumps(prof, ensure_ascii=False)[:300])
        diary = self._load("diary")
        if diary:
            rows = []
            for r in diary[-limit:]:
                t = str(r.get("text", ""))
                if t.startswith("用户说："):
                    t = t[len("用户说："):]
                if "；她回：" in t:
                    t = t.split("；她回：", 1)[0]
                rows.append(t[:100])
            parts.append("最近的日记（只取用户说过的）：" + " / ".join(rows))
        return "\n".join(parts)

    def consolidate(self) -> None:
        rows = self._load("diary")
        self._save("diary", rows[-200:])
        learning = self._load("learning")
        items = list(learning.items())[-200:]
        self._save("learning", dict(items))

    def audit(self, event: Dict[str, object]) -> None:
        with (self.root / "audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), **event}, ensure_ascii=False) + "\n")


def default_responder(text: str, emotion: str, affect: Dict[str, object], recall: str) -> List[str]:
    v = float(affect.get("v", 0.0))
    if emotion in ("sadness", "anxiety", "loneliness", "anger"):
        head = "我在。" if v < 0.2 else "我听着呢。"
        return [head, "你慢慢说，不用一个人扛。"]
    if emotion == "joy":
        return ["听起来真好。", "我也替你开心。"]
    return ["我在。", "你继续说。"]


@dataclass
class RuntimeConfig:
    cooldown: float = 1.0
    max_chars: int = 1200
    proactive_quiet_s: float = 3600.0
    proactive_cooldown: float = 7200.0
    token_budget: float = 1.0


class HomeostaticClock:
    """Heartbeat interval from energy/stress, not a fixed cron."""

    def __init__(self, base_interval: float = 1.0):
        self.base_interval = max(0.05, float(base_interval))

    def interval(self, mind) -> float:
        try:
            energy = float(mind.drives.values.get("energy", 0.5))
            stress = float(mind.affect.stress)
        except Exception:
            energy, stress = 0.5, 0.0
        return clip(self.base_interval * (1.0 + 1.5 * stress + 1.0 * (1.0 - energy)), 0.2, 10.0)


class MetabolicBudget:
    """Token budget as metabolism: low energy shortens/denies replies."""

    def __init__(self, energy: float = 1.0):
        self.energy = clip(float(energy), 0.0, 1.0)

    def tick(self, dt: float) -> None:
        self.energy = clip(self.energy + 0.03 * float(dt) - 0.005, 0.0, 1.0)

    def can_respond(self) -> bool:
        return self.energy > 0.10

    def spend(self, cost: float = 0.02) -> None:
        self.energy = clip(self.energy - float(cost), 0.0, 1.0)

    def length_scale(self) -> float:
        return clip(0.6 + 1.4 * self.energy, 0.6, 2.0)


class FeedbackLearner:
    """Updates empathy gain and proactive threshold from real feedback."""

    def __init__(self, path: Optional[Path | str] = None, lr: float = 0.05):
        self.path = Path(path) if path else None
        self.lr = float(lr)
        self.empathy_gain = 0.35
        self.proactive_threshold = 0.65
        self.count = 0
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.empathy_gain = float(data.get("empathy_gain", self.empathy_gain))
            self.proactive_threshold = float(data.get("proactive_threshold", self.proactive_threshold))
            self.count = int(data.get("count", 0))
        except Exception:
            pass

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "empathy_gain": round(self.empathy_gain, 6),
            "proactive_threshold": round(self.proactive_threshold, 6),
            "count": self.count,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    def update(self, reward: float, proactive: bool = False) -> Dict[str, float]:
        r = clip(float(reward), 0.0, 1.0)
        err = r - 0.5
        self.empathy_gain = clip(self.empathy_gain + self.lr * err, 0.05, 0.85)
        if proactive:
            self.proactive_threshold = clip(self.proactive_threshold - self.lr * err, 0.35, 0.95)
        self.count += 1
        self.save()
        return {"empathy_gain": round(self.empathy_gain, 4),
                "proactive_threshold": round(self.proactive_threshold, 4)}


class HeartbeatRuntime:
    def __init__(self, mind, store: Optional[MemoryStore] = None,
                 responder: Optional[Callable] = None,
                 sender: Optional[Callable[[List[str]], None]] = None,
                 cfg: Optional[RuntimeConfig] = None,
                 learner: Optional[FeedbackLearner] = None,
                 budget: Optional[MetabolicBudget] = None,
                 perceiver: Optional[Callable] = None,
                 perceiver_affect: Optional[Callable] = None):
        self.mind = mind
        self.store = store or MemoryStore()
        self.responder = responder or default_responder
        self.sender = sender or (lambda lines: None)
        self.cfg = cfg or RuntimeConfig()
        self.learner = learner or FeedbackLearner()
        self.budget = budget or MetabolicBudget()
        self.clock = HomeostaticClock()
        self.perceiver = perceiver or perceive_emotion_lexicon
        self.perceiver_affect = perceiver_affect
        self.pending: List[Dict[str, str]] = []
        self.last_emotion: str = "neutral"
        self.last_user_id: str = "default"
        self.proactive_sent = 0
        self.proactive_feedback = 0
        self.proactive_accepted = 0
        self.last_sent = -1e9
        self.last_user = -1e9
        self.last_proactive = -1e9
        self.last_state: Dict[str, object] = {}

    def receive(self, text: str, user_id: str = "default") -> bool:
        t = str(text or "").strip()
        black = set(str(x) for x in (self.store._load("blacklist") or {}).values())
        if not t or t in black:
            return False
        self.pending.append({"text": t, "user_id": str(user_id)})
        self.last_user = time.time()
        return True

    def tick(self, now: Optional[float] = None) -> Optional[Dict[str, object]]:
        now = time.time() if now is None else float(now)
        dt = 1.0 if self.last_sent < -1e8 else max(0.0, now - self.last_sent)
        self.budget.tick(dt)
        if not self.pending or now - self.last_sent < self.cfg.cooldown:
            return None
        if not self.budget.can_respond():
            self.store.audit({"kind": "budget_block", "energy": round(self.budget.energy, 3)})
            return None
        merged = " ".join(p["text"] for p in self.pending)[-self.cfg.max_chars:]
        user_id = self.pending[-1]["user_id"]
        self.pending.clear()
        try:
            from .types import Event
            self.mind.step(Event(text=merged, speaker=user_id, directed=True,
                                 ts=self.mind.t + 60))
        except Exception:
            pass
        read = None
        if self.perceiver_affect is not None:
            try:
                read = self.perceiver_affect(merged)
                self.mind.inject_affect(read.get("valence", 0.0), read.get("arousal", 0.5),
                                        read.get("dominance", 0.0), read.get("intensity", 0.7))
                from .emotion_schema import emotion_readout, top_emotions
                ro = emotion_readout(read.get("valence", 0.0), read.get("arousal", 0.5),
                                     read.get("dominance", 0.0))
                top = top_emotions(ro, 1)
                emotion = str(top[0][0]) if top else "neutral"
                intensity = float(read.get("intensity") or 0.5)
            except Exception:
                read = None
        if read is None:
            emotion, intensity = self.perceiver(merged)
            self.mind.empathize(emotion, intensity, empathy_gain=self.learner.empathy_gain)
        self.last_emotion = str(emotion)
        self.last_user_id = str(user_id)
        recall = self.store.recall(user_id)
        snap = self.mind.snapshot()
        lines = self.responder(merged, emotion, snap.get("affect", {}), recall)
        lines = [str(x) for x in lines if str(x).strip()][:6]
        self.sender(lines)
        self.budget.spend()
        self.store.append_diary(f"用户说：{merged[:400]}；她回：{' / '.join(lines)[:400]}", tags=[emotion])
        self.store.audit({"kind": "tick", "emotion": emotion, "intensity": intensity,
                          "reply": lines, "empathy_gain": self.learner.empathy_gain,
                          "energy": round(self.budget.energy, 3)})
        self.last_sent = now
        self.last_state = {"emotion": emotion, "reply": lines, "recall": recall,
                           "empathy_gain": self.learner.empathy_gain,
                           "affect": read or {}}
        return self.last_state

    def feedback(self, reward: float, text: str = "", user_id: str = "default",
                 proactive: bool = False) -> Dict[str, float]:
        if proactive:
            self.proactive_feedback += 1
            if float(reward) >= 0.5:
                self.proactive_accepted += 1
        try:
            self.mind.post_reply("", feedback=float(reward),
                                  speaker=self.last_user_id or "web", responded=True)
        except Exception:
            pass
        out = self.learner.update(reward, proactive=proactive)
        self.store.learn(f"反馈 {float(reward):.2f}: {str(text)[:200]}", reward=float(reward))
        self.store.audit({"kind": "feedback", "reward": float(reward), "proactive": proactive, **out})
        return out

    def proactive(self, now: Optional[float] = None, force: bool = False) -> Optional[List[str]]:
        now = time.time() if now is None else float(now)
        if self.pending:
            return None
        if not force and now - self.last_user < self.cfg.proactive_quiet_s:
            return None
        if not force and now - self.last_proactive < self.cfg.proactive_cooldown:
            return None
        if not self.budget.can_respond():
            return None
        try:
            social = float(self.mind.drives.values.get("social", 0.5))
        except Exception:
            social = 0.5
        if social < self.learner.proactive_threshold:
            return None
        recall = self.store.recall(self.last_user_id)
        if self.last_emotion in ("sadness", "anxiety", "loneliness", "fear"):
            lines = ["刚才你说有点难受，", "现在好一点了吗？我在。"]
        elif self.last_emotion == "anger":
            lines = ["刚才那件事，", "你还在生气吗？我在这儿。"]
        elif recall:
            lines = ["在吗？", "突然想起你之前说的，想问问你现在怎么样了。"]
        else:
            lines = ["在吗？", "突然想跟你说句话。"]
        self.sender(lines)
        self.budget.spend()
        self.last_proactive = now
        self.proactive_sent += 1
        self.store.audit({"kind": "proactive", "social": social, "reply": lines,
                          "threshold": self.learner.proactive_threshold,
                          "emotion": self.last_emotion, "recall": recall[:200]})
        return lines

    def send_generated(self, reply: str) -> None:
        text = str(reply or "").strip()
        if not text:
            return
        self.sender([text])
        self.budget.spend()
        self.last_proactive = time.time()
        self.proactive_sent += 1
        self.store.audit({"kind": "proactive_generated", "reply": text,
                          "emotion": self.last_emotion})

    def health(self) -> Dict[str, object]:
        try:
            energy = float(self.mind.drives.values.get("energy", 0.5))
            stress = float(self.mind.affect.stress)
        except Exception:
            energy, stress = 0.5, 0.0
        return {"energy": round(energy, 3), "stress": round(stress, 3),
                "budget": round(self.budget.energy, 3),
                "interval": round(self.clock.interval(self.mind), 3),
                "empathy_gain": round(self.learner.empathy_gain, 3),
                "proactive_threshold": round(self.learner.proactive_threshold, 3),
                "proactive_sent": self.proactive_sent,
                "proactive_feedback": self.proactive_feedback,
                "proactive_accepted": self.proactive_accepted}
