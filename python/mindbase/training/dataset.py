from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from ..actions import kind_for_style

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "story_annotations.json"
ACTION_LABELS = ("speak", "silence", "approach", "avoid", "repair")


@dataclass
class StorySample:
    event: str
    state: Dict[str, float]
    appraisal: Dict[str, float]
    action: str
    outcome: Dict[str, float]
    reward: float
    group: str = ""
    domain: str = "general"
    action_kind: str = ""
    modality: str = "none"
    structured: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "StorySample":
        return cls(
            event=str(d["event"]),
            state={k: float(v) for k, v in d.get("state", {}).items()},
            appraisal={k: float(v) for k, v in d.get("appraisal", {}).items()},
            action=str(d["action"]),
            outcome={k: float(v) for k, v in d.get("outcome", {}).items()},
            reward=float(d.get("reward", 0.0)),
            group=str(d.get("group", "")),
            domain=str(d.get("domain", "general")),
            action_kind=str(d.get("action_kind") or kind_for_style(str(d.get("action", "")))),
            modality=str(d.get("modality", "none")),
            structured={k: float(v) for k, v in (d.get("structured") or {}).items()},
        )


def load_samples(path: Path | None = None) -> List[StorySample]:
    raw = json.loads(Path(path or DATA_PATH).read_text(encoding="utf-8"))
    return [StorySample.from_dict(x) for x in raw]


def train_test_split(samples: Sequence[StorySample], test_ratio: float = 0.2, seed: int = 0) -> Tuple[List[StorySample], List[StorySample]]:
    rows = list(samples)
    random.Random(seed).shuffle(rows)
    n_test = max(1, int(round(len(rows) * test_ratio)))
    return rows[:-n_test], rows[-n_test:]
