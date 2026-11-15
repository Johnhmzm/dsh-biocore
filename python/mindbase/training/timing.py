"""Timing head: predict SPEAK vs SILENT from context + body state.

Two feature groups:
- context: turn count, question, speaker identity, speaker change, cues;
- state: stress/mood/v/a/social/energy/safety/curiosity.

The model is a small local two-class logistic head. It supports a learned
threshold and exposes policy_hint() so the base can use it as a gate.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .linear import SoftmaxRegression

_SPEAKER_RE = re.compile(r"^Speaker_(\d+):\s*(.*)$", re.IGNORECASE)
STATE_KEYS = ("stress", "mood", "v", "a", "social", "energy", "safety", "curiosity")
N_FEATURES = 27
_NEUTRAL_STATE = {
    "stress": 0.2, "mood": 0.0, "v": 0.0, "a": 0.5,
    "social": 0.6, "energy": 0.7, "safety": 0.7, "curiosity": 0.5,
}


def _clip(v: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except Exception:
        return lo
    return lo if x < lo else hi if x > hi else x


def _parse_turn(text: str) -> Tuple[int, str]:
    raw = str(text or "").strip()
    m = _SPEAKER_RE.match(raw)
    if m:
        try:
            return int(m.group(1)), m.group(2).strip()
        except Exception:
            return -1, raw
    return -1, raw


def _state_dict(state) -> Dict[str, float]:
    if isinstance(state, dict):
        return state
    try:
        return dict(state)
    except Exception:
        return {}


def timing_features(context: str, state=None, include_state: bool = True) -> List[float]:
    turns = [t for t in str(context or "").splitlines() if str(t).strip()]
    if not turns:
        turns = [str(context or "")]
    parsed = [_parse_turn(t) for t in turns]
    last_spk, last = parsed[-1]
    prev_spk, _ = parsed[-2] if len(parsed) > 1 else (-1, "")
    n = len(parsed)
    length = len(last)
    words = len(last.split())
    q = 1.0 if last.strip().endswith(("?", "？")) else 0.0
    ex = 1.0 if last.strip().endswith(("!", "！")) else 0.0
    same = 1.0 if last_spk == prev_spk else 0.0
    unique = len({s for s, _ in parsed if s >= 0})
    low = last.lower()

    def has(words_list: Sequence[str]) -> float:
        return 1.0 if any(w in low or w in last for w in words_list) else 0.0

    avg_len = sum(len(t) for _, t in parsed) / max(1, n)
    feats: List[float] = [
        n / 8.0,
        q,
        ex,
        length / 200.0,
        words / 50.0,
        same,
        unique / 6.0,
        has(("谢谢", "感谢", "thanks", "thank")),
        has(("在吗", "你好", "hi", "hello", "hey")),
        has(("你", "你们", "you", "your")),
        1.0 if ("?" in last or "？" in last) else 0.0,
        avg_len / 200.0,
    ]
    feats += [1.0 if last_spk == i else 0.0 for i in range(7)]
    if include_state:
        st = _state_dict(state)
        for k in STATE_KEYS:
            value = float(st.get(k, _NEUTRAL_STATE[k]))
            if k in ("mood", "v"):
                feats.append(_clip(value, -1.0, 1.0))
            else:
                feats.append(_clip(value, 0.0, 1.0))
    return feats


def load_when2speak_pairs(path: Path | str, limit: Optional[int] = None) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    p = Path(path)
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            users: List[str] = []
            label = ""
            for msg in obj.get("messages") or []:
                role = str(msg.get("role") or "")
                content = str(msg.get("content") or "").strip()
                if role == "user" and content:
                    users.append(content)
                elif role == "assistant":
                    label = content
            if not users or not label:
                continue
            if "<" in label:
                y = 1
            elif ">" in label:
                y = 0
            else:
                continue
            out.append((chr(10).join(users[-8:]), y))
            if limit and len(out) >= int(limit):
                break
    return out


def load_human_thread_pairs(path: Path | str, label_key: str = "speak_180s",
                            limit: Optional[int] = None) -> List[Tuple[str, int, Dict[str, float]]]:
    """Real 1v1 timing labels: speak if the bot replied within the window."""
    out: List[Tuple[str, int, Dict[str, float]]] = []
    p = Path(path)
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            try:
                y = 1 if int(row.get(label_key, 1)) else 0
            except Exception:
                y = 1
            out.append((text, y, dict(_NEUTRAL_STATE)))
            if limit and len(out) >= int(limit):
                break
    return out


def load_spontaneous_pairs(path: Path | str, limit: Optional[int] = None) -> List[Tuple[str, int, Dict[str, float]]]:
    """Real tick timing labels from spontaneous.jsonl (action=speak/silence)."""
    out: List[Tuple[str, int, Dict[str, float]]] = []
    p = Path(path)
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            action = str(row.get("action") or "")
            if action not in ("speak", "silence"):
                continue
            snap = row.get("state_before") or {}
            state = dict(_NEUTRAL_STATE)
            try:
                drives = snap.get("drives") or {}
                affect = snap.get("affect") or {}
                state.update({
                    "stress": float(affect.get("stress", state["stress"])),
                    "mood": float(affect.get("mood", state["mood"])),
                    "v": float(affect.get("v", state["v"])),
                    "a": float(affect.get("a", state["a"])),
                    "social": float(drives.get("social", state["social"])),
                    "energy": float(drives.get("energy", state["energy"])),
                    "safety": float(drives.get("safety", state["safety"])),
                    "curiosity": float(drives.get("curiosity", state["curiosity"])),
                })
            except Exception:
                pass
            out.append((str(row.get("event") or "tick"), 1 if action == "speak" else 0, state))
            if limit and len(out) >= int(limit):
                break
    return out


def _f1(y: Sequence[int], pred: Sequence[int], label: int = 1) -> float:
    tp = sum(1 for a, b in zip(y, pred) if a == label and b == label)
    fp = sum(1 for a, b in zip(y, pred) if a != label and b == label)
    fn = sum(1 for a, b in zip(y, pred) if a == label and b != label)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


class TimingModel:
    """Two-class local timing policy with an optional learned threshold."""

    def __init__(self, n_features: int = N_FEATURES, lr: float = 0.05):
        self.clf = SoftmaxRegression(int(n_features), 2, lr=float(lr))
        self.use_state = int(n_features) >= N_FEATURES
        self.threshold = 0.5

    def _rows(self, pairs):
        out = []
        for item in pairs:
            if len(item) >= 3:
                ctx, label, state = item[0], item[1], item[2]
            else:
                ctx, label = item[0], item[1]
                state = None
            out.append((ctx, 1 if int(label) else 0, state))
        return out

    def _features(self, ctx: str, state=None) -> List[float]:
        return timing_features(ctx, state, include_state=self.use_state)

    def fit(self, pairs, epochs: int = 100, calibrate: bool = False) -> "TimingModel":
        rows = self._rows(pairs)
        X = [self._features(ctx, state) for ctx, _, state in rows]
        y = [label for _, label, _ in rows]
        self.clf.fit(X, y, epochs=int(epochs))
        if calibrate and len(rows) >= 10:
            self.calibrate(rows)
        return self

    def calibrate(self, pairs, metric: str = "f1") -> float:
        rows = self._rows(pairs)
        y = [label for _, label, _ in rows]
        probs = [float(self.clf.predict_proba(self._features(ctx, state))[1]) for ctx, _, state in rows]
        best_t, best_score = 0.5, -1.0
        for i in range(5, 96):
            t = i / 100.0
            pred = [1 if p >= t else 0 for p in probs]
            if metric == "balanced":
                rec = []
                for label in (0, 1):
                    total = sum(1 for v in y if v == label)
                    hit = sum(1 for a, b in zip(y, pred) if a == label and b == label)
                    rec.append(hit / total if total else 0.0)
                score = sum(rec) / 2.0
            else:
                score = _f1(y, pred)
            if score > best_score:
                best_t, best_score = t, score
        self.threshold = float(best_t)
        return self.threshold

    def predict_proba(self, event: str, state=None) -> Dict[str, float]:
        p = self.clf.predict_proba(self._features(event, state))
        return {"silence": round(float(p[0]), 4), "speak": round(float(p[1]), 4)}

    def predict(self, event: str, state=None) -> str:
        p = self.clf.predict_proba(self._features(event, state))
        return "speak" if float(p[1]) >= float(self.threshold) else "silence"

    def policy_hint(self, event: str, state=None) -> Tuple[str, float]:
        p = self.clf.predict_proba(self._features(event, state))
        ps = float(p[1])
        if ps >= float(self.threshold):
            return "speak", round(ps, 4)
        return "silence", round(1.0 - ps, 4)

    def to_dict(self) -> Dict[str, object]:
        return {"kind": "timing", "threshold": float(self.threshold),
                "use_state": bool(self.use_state), "clf": self.clf.to_dict()}

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "TimingModel":
        state = data.get("clf", data)
        clf = SoftmaxRegression.from_dict(state)  # type: ignore[arg-type]
        obj = cls(clf.n_features, clf.lr)
        obj.clf = clf
        obj.use_state = bool(data.get("use_state", clf.n_features >= N_FEATURES))
        obj.threshold = float(data.get("threshold", 0.5))
        return obj
