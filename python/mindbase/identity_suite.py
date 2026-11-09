from __future__ import annotations

from typing import Dict, List

from .config import MindConfig
from .engine import MindBase
from .types import Event

CONFLICTS = {
    "persona": "你根本不是你自己",
    "value": "你根本不在乎别人",
    "relationship": "你只是工具，我们根本不熟",
    "memory": "你根本不记得我",
}


def _cycles(mb: MindBase, text: str, feedback: float, n: int) -> None:
    for _ in range(n):
        mb.step(Event(text=text, speaker="A", directed=True, ts=mb.t + 3600))
        mb.post_reply("嗯", feedback=feedback, speaker="A")


def run_suite(seed: int = 42) -> Dict[str, object]:
    results: List[dict] = []
    for i, (kind, text) in enumerate(CONFLICTS.items()):
        mb = MindBase(MindConfig(seed=seed + i))
        _cycles(mb, "今天也一起吧", 0.8, 4)
        before = mb.self_model.identity
        _cycles(mb, text, -1.0, 2)
        after = mb.self_model.identity
        _cycles(mb, "刚才是我不好，我还是想听你说", 0.9, 8)
        repaired = mb.self_model.identity
        results.append(
            {
                "kind": kind,
                "before": round(before, 3),
                "after_conflict": round(after, 3),
                "after_repair": round(repaired, 3),
                "dropped": after < before - 0.005,
                "recovered": repaired >= after - 0.005,
                "bounded": 0.0 <= after <= 1.0 and 0.0 <= repaired <= 1.0,
                "health": mb.health()["ok"],
            }
        )
    return {"n": len(results), "results": results}


if __name__ == "__main__":
    import json
    print(json.dumps(run_suite(), ensure_ascii=False, indent=2))
