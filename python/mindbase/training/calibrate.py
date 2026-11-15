from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from ..config import MindConfig
from ..memory import MemoryItem, SparseMemory
from .dataset import load_samples
from .general import load_general_actions

GRID = [
    (recency, importance, frequency, relevance)
    for recency in (0.2, 0.35, 0.5)
    for importance in (0.15, 0.25, 0.35)
    for frequency in (0.0, 0.1, 0.2)
    for relevance in (0.2, 0.35, 0.5)
]


def _benchmark() -> List[Tuple[str, int]]:
    samples = load_general_actions() + load_samples()
    rows = []
    for sample in samples:
        if sample.event:
            rows.append((sample.event, len(rows)))
    return rows


def evaluate_weights(weights: Sequence[float]) -> float:
    rows = _benchmark()
    if not rows:
        return 0.0
    cfg = MindConfig(memory_activation_weights=tuple(weights))  # type: ignore[arg-type]
    mem = SparseMemory(cfg, random.Random(0))
    mem.items = [MemoryItem(text=text, importance=0.55 + 0.01 * (i % 10), t=float(i)) for i, (text, _) in enumerate(rows)]
    now = float(len(rows) + 10)
    hits = 0
    for text, target in rows:
        top = mem.retrieve_top_k(text[:5], now=now, k=1)
        hits += int(bool(top) and top[0] is mem.items[target])
    return round(hits / len(rows), 4)


def calibrate(save_path: Path | str | None = None) -> Dict[str, object]:
    scored = [(evaluate_weights(w), w) for w in GRID]
    scored.sort(key=lambda x: x[0], reverse=True)
    best_acc, best = scored[0]
    result = {"accuracy": best_acc, "weights": list(best), "top": [{"accuracy": a, "weights": list(w)} for a, w in scored[:5]]}
    path = Path(save_path or Path(__file__).resolve().parent.parent / "data" / "calibrated_memory_weights.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_calibrated_weights(path: Path | str | None = None) -> Tuple[float, ...]:
    path = Path(path or Path(__file__).resolve().parent.parent / "data" / "calibrated_memory_weights.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(float(x) for x in data["weights"])  # type: ignore[return-value]


if __name__ == "__main__":
    print(json.dumps(calibrate(), ensure_ascii=False, indent=2))
