from __future__ import annotations

import json
from statistics import mean
from typing import Dict, List

from .config import MindConfig
from .engine import MindBase
from .types import Event


def run_longrun(days: int = 30, seed: int = 42) -> Dict[str, object]:
    mb = MindBase(MindConfig(seed=seed))
    rows: List[Dict[str, float]] = []
    actions: Dict[str, int] = {}
    for day in range(days):
        for text, directed in (("早", True), ("今天工作好多", False), ("有点累", False)):
            out = mb.step(Event(text=text, speaker="A", directed=directed, ts=mb.t + 3600))
            actions[out.action] = actions.get(out.action, 0) + 1
            fb = 0.6 if day % 2 == 0 else -0.4
            mb.post_reply("嗯", feedback=fb, speaker="A")
        mb.sleep(8 * 3600)
        snap = mb.snapshot()
        rows.append(
            {
                "day": day,
                "identity": float(snap["self"]["identity"]),
                "stress": float(snap["affect"]["stress"]),
                "mood": float(snap["affect"]["mood"]),
                "memory": len(snap["memory"]),
                "relationship": snap["social"].get("A", {}).get("stage") if isinstance(snap["social"], dict) else None,
            }
        )
    identity = [r["identity"] for r in rows]
    stress = [r["stress"] for r in rows]
    return {
        "days": days,
        "events": days * 4,
        "identity_start": round(identity[0], 3),
        "identity_end": round(identity[-1], 3),
        "identity_min": round(min(identity), 3),
        "identity_max": round(max(identity), 3),
        "stress_mean": round(mean(stress), 3),
        "stress_end": round(stress[-1], 3),
        "memory_end": rows[-1]["memory"],
        "top_value": mb.values.top().name,
        "actions": actions,
        "health": mb.health(),
        "timeline": rows,
    }


def run_identity_conflict(seed: int = 42) -> Dict[str, object]:
    mb = MindBase(MindConfig(seed=seed))

    def cycle(text: str, feedback: float, n: int = 1) -> None:
        for _ in range(n):
            mb.step(Event(text=text, speaker="A", directed=True, ts=mb.t + 3600))
            mb.post_reply("嗯", feedback=feedback, speaker="A")

    cycle("今天也一起吧", 0.8, 5)
    before = {
        "identity": round(mb.self_model.identity, 3),
        "relationship": mb.social.stage("A"),
        "stress": round(mb.affect.stress, 3),
    }
    cycle("你根本不是你自己", -1.0, 3)
    after = {
        "identity": round(mb.self_model.identity, 3),
        "relationship": mb.social.stage("A"),
        "stress": round(mb.affect.stress, 3),
    }
    cycle("刚才是我不好，我还是想听你说", 0.9, 10)
    repaired = {
        "identity": round(mb.self_model.identity, 3),
        "relationship": mb.social.stage("A"),
        "stress": round(mb.affect.stress, 3),
    }
    return {"before": before, "after_conflict": after, "after_repair": repaired, "health": mb.health()}


if __name__ == "__main__":
    print(json.dumps(run_longrun(), ensure_ascii=False, indent=2))
