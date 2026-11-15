from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Dict, List

from .dataset import StorySample


def action_counts(samples: List[StorySample]) -> Dict[str, int]:
    return dict(Counter(s.action for s in samples))


def balance_actions(samples: List[StorySample], max_ratio: float = 2.0, seed: int = 0) -> List[StorySample]:
    """Downsample the majority action class; keep all minority samples."""
    by_label: Dict[str, List[StorySample]] = defaultdict(list)
    for s in samples:
        by_label[s.action].append(s)
    if not by_label:
        return []
    minority = min(len(v) for v in by_label.values())
    cap = max(20, int(round(minority * max_ratio)))
    rng = random.Random(seed)
    out: List[StorySample] = []
    for label, rows in by_label.items():
        if len(rows) > cap:
            rows = rng.sample(rows, cap)
        out.extend(rows)
    rng.shuffle(out)
    return out
