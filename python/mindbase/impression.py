"""Dynamic, evidence-weighted impression of a user.

This is not a permanent tag. It is a slow, decaying state built from many turns:
  summary + confidence + evidence_count + numeric dimensions + recent evidence
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


@dataclass
class Impression:
    summary: str = ""
    confidence: float = 0.0
    evidence_count: int = 0
    valence_mean: float = 0.0
    familiarity: float = 0.0
    directness: float = 0.5
    sensitivity: float = 0.5
    last_updated: float = 0.0
    evidence: List[str] = field(default_factory=list)

    def observe(self, text: str, reply: str, affect: dict | None = None,
                reward: float | None = None, trust: float = 0.5,
                stage: str = "stranger", now: float | None = None) -> "Impression":
        now = float(now if now is not None else time.time())
        t = str(text or "").strip()
        if not t:
            return self
        affect = affect or {}
        v = float(affect.get("v", 0.0) if "v" in affect else affect.get("valence", 0.0))
        r = float(reward if reward is not None else 0.5)
        self.evidence_count += 1
        n = self.evidence_count
        self.valence_mean = _clip((self.valence_mean * (n - 1) + (0.6 * v + 0.4 * (2 * r - 1))) / n, -1.0, 1.0)
        fam = {"stranger": 0.05, "acquaintance": 0.25, "familiar": 0.5,
               "trusted": 0.75, "intimate": 0.95}.get(str(stage), 0.1)
        self.familiarity = _clip(0.8 * self.familiarity + 0.2 * max(fam, float(trust)))
        length = len(t)
        imperative = any(w in t for w in ("别", "快", "去", "说", "来", "闭嘴", "滚"))
        self.directness = _clip(0.85 * self.directness + 0.15 * (0.65 if imperative or length < 12 else 0.45))
        neg = sum(1 for w in ("累", "烦", "难受", "压力", "不开心", "崩溃", "讨厌", "滚") if w in t)
        self.sensitivity = _clip(0.85 * self.sensitivity + 0.15 * (0.75 if neg else 0.35))
        self.confidence = _clip(0.1 + 0.08 * math.sqrt(n), 0.0, 0.95)
        self.last_updated = now
        snippet = t[:80]
        if snippet not in self.evidence:
            self.evidence = (self.evidence + [snippet])[-6:]
        tone = "偏暖" if self.valence_mean > 0.12 else ("有摩擦" if self.valence_mean < -0.12 else "中性")
        direct = "直接" if self.directness > 0.55 else "迂回"
        sens = "最近压力偏高" if self.sensitivity > 0.55 else "情绪较平稳"
        self.summary = ("互动%s；表达%s；%s；熟悉度%.2f；置信度%.2f"
                        % (tone, direct, sens, self.familiarity, self.confidence))
        return self

    def decay(self, now: float | None = None, half_life_days: float = 7.0) -> "Impression":
        now = float(now if now is not None else time.time())
        if not self.last_updated:
            return self
        days = max(0.0, (now - self.last_updated) / 86400.0)
        factor = 0.5 ** (days / max(0.5, half_life_days))
        self.familiarity = _clip(self.familiarity * factor)
        self.confidence = _clip(self.confidence * factor)
        return self

    def context(self) -> str:
        if not self.evidence_count:
            return ""
        ev = " / ".join(self.evidence[-3:])
        return ("%s；维度：暖度%.2f 直接度%.2f 敏感度%.2f；最近证据：%s"
                % (self.summary, self.valence_mean, self.directness,
                   self.familiarity, ev))

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class ImpressionStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get(self, user_id: str) -> Impression:
        raw = self._load().get(str(user_id)) or {}
        imp = Impression()
        if isinstance(raw, dict):
            for k in ("summary", "confidence", "evidence_count", "valence_mean",
                      "familiarity", "directness", "sensitivity", "last_updated", "evidence"):
                if k in raw:
                    try:
                        setattr(imp, k, raw[k])
                    except Exception:
                        pass
        imp.decay()
        return imp

    def save(self, user_id: str, imp: Impression) -> None:
        data = self._load()
        data[str(user_id)] = imp.to_dict()
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
