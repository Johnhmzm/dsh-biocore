from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

DATA = Path(__file__).resolve().parent / "data"
EPISODES = DATA / "episodes.jsonl"


@dataclass
class Episode:
    ts: float
    group_id: str
    user_id: str
    text: str
    reply: str
    trust: float
    identity: float
    stage: str
    state: Dict[str, float]
    feedback: float = 0.2
    memory_prediction: Optional[str] = None
    metrics: Optional[Dict[str, float]] = None
    engine: str = "legacy"

    def to_row(self) -> Dict[str, object]:
        return asdict(self)


def append_episode(ep: Episode, path: Path = EPISODES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ep.to_row(), ensure_ascii=False) + "\n")


def load_episodes(path: Path = EPISODES) -> List[Dict[str, object]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _question_rate(reply: str) -> float:
    return 1.0 if ("?" in reply or "？" in reply) else 0.0


def evaluate(rows: List[Dict[str, object]]) -> Dict[str, object]:
    if not rows:
        return {"n": 0}
    lengths = [len(str(r.get("reply", ""))) for r in rows]
    questions = [_question_rate(str(r.get("reply", ""))) for r in rows]
    trust = [float(r.get("trust", 0.0)) for r in rows]
    identity = [float(r.get("identity", 0.0)) for r in rows]
    memory_hits = sum(1 for r in rows if r.get("memory_prediction"))
    # engagement = a later user message from the same group within 180s
    engaged = 0
    for i, r in enumerate(rows):
        t = float(r.get("ts", 0.0))
        gid = str(r.get("group_id", ""))
        for later in rows[i + 1:]:
            if str(later.get("group_id", "")) == gid and 0 < float(later.get("ts", 0.0)) - t <= 180:
                engaged += 1
                break
    engines = {}
    for r in rows:
        key = str(r.get("engine") or "legacy")
        engines[key] = engines.get(key, 0) + 1
    return {
        "n": len(rows),
        "engines": engines,
        "reply_len_mean": round(sum(lengths) / len(lengths), 2),
        "question_rate": round(sum(questions) / len(questions), 3),
        "memory_hit_rate": round(memory_hits / len(rows), 3),
        "trust_mean": round(sum(trust) / len(trust), 3),
        "identity_mean": round(sum(identity) / len(identity), 3),
        "engagement_rate": round(engaged / len(rows), 3),
    }


def report(path: Path = EPISODES) -> Dict[str, object]:
    return evaluate(load_episodes(path))
