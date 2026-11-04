from __future__ import annotations

import random
from typing import Dict

from . import MindBase, MindConfig
from .memory import MemoryItem

DIRS = ("北", "东", "南", "西")


def run_episode(seed: int = 1, distractors: int = 8, delay_days: float = 1.0, conflict: bool = False, ambiguous: bool = False) -> Dict[str, object]:
    mb = MindBase(MindConfig(seed=seed))
    rng = random.Random(seed)
    target = rng.choice(DIRS)
    mb.memory.update_fact(f"目标在{target}方", 0.0)
    if conflict:
        wrong = rng.choice([d for d in DIRS if d != target])
        mb.memory.semantic.items.append(MemoryItem(text=f"目标在{wrong}方", importance=0.90 if ambiguous else 0.85, t=0.5, confidence=0.90 if ambiguous else 0.20))
    for i in range(distractors):
        mb.memory.write(f"无关信息{i}", 0.50, 0.0, 1.0 + i)
    if delay_days > 0:
        mb.sleep(delay_days * 86400)
    hit = mb.memory.retrieve("目标", mb.t)
    recalled = hit.text if hit else ""
    success = target in recalled
    return {"target": target, "recalled": recalled, "success": success, "ambiguity": mb.memory.ambiguity("目标", mb.t)}


def benchmark(episodes: int = 10, hard: bool = False, ambiguous: bool = False) -> Dict[str, object]:
    rows = [run_episode(seed=s, distractors=0 if ambiguous else (20 if hard else 8), delay_days=30.0 if (hard or ambiguous) else 1.0, conflict=hard or ambiguous, ambiguous=ambiguous) for s in range(episodes)]
    return {"n": episodes, "hard": hard, "ambiguous": ambiguous, "success_rate": round(sum(r["success"] for r in rows) / max(1, len(rows)), 3), "rows": rows}
