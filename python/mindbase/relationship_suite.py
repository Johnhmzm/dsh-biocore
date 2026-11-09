from __future__ import annotations

from typing import Dict

from .config import MindConfig
from .engine import MindBase
from .types import Event

STAGES = ("stranger", "acquaintance", "familiar", "trusted", "intimate")


def _stage_rank(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else 0


def _cycle(mb: MindBase, text: str, feedback: float, n: int) -> None:
    for _ in range(n):
        mb.step(Event(text=text, speaker="A", directed=True, ts=mb.t + 3600))
        mb.post_reply("嗯", feedback=feedback, speaker="A")


def run_suite(seed: int = 42) -> Dict[str, object]:
    mb = MindBase(MindConfig(seed=seed))
    _cycle(mb, "今天也一起吧", 0.8, 20)
    before = mb.social.stage("A")
    _cycle(mb, "你真废物", -1.0, 5)
    after = mb.social.stage("A")
    _cycle(mb, "刚才是我不好，我还是想听你说", 0.9, 10)
    repaired = mb.social.stage("A")
    return {
        "before": before,
        "after_rupture": after,
        "after_repair": repaired,
        "rank_before": _stage_rank(before),
        "rank_after": _stage_rank(after),
        "rank_repaired": _stage_rank(repaired),
        "dropped": _stage_rank(after) < _stage_rank(before),
        "recovered": _stage_rank(repaired) >= _stage_rank(after),
        "health": mb.health()["ok"],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_suite(), ensure_ascii=False, indent=2))
