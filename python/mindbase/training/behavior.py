from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .dataset import StorySample

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR" / "annotations"
STATE = {"stress": 0.3, "social": 0.3, "mood": 0.0, "energy": 0.6, "identity": 0.7}


def load_annotations(path: Path | str = DATA, limit: int | None = None, history: int = 0) -> List[StorySample]:
    out: List[StorySample] = []
    for file in sorted(Path(path).rglob("*.json")):
        try:
            row = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        skills = row.get("skill_annotation") or []
        task = str(row.get("task_name") or file.stem)
        previous: List[str] = []
        for i, skill in enumerate(skills):
            desc = " ".join(str(x) for x in (skill.get("skill_description") or []))
            objects = " ".join(str(x) for x in (skill.get("object_id") or []))
            frames = skill.get("frame_duration") or [0, 0]
            kind = desc or "unknown"
            event = f"{task}; {desc}; objects: {objects}; frames: {frames[0]}-{frames[1]}"
            if history and previous:
                event = "previous: " + " | ".join(previous[-history:]) + " || " + event
            out.append(
                StorySample(
                    event=event,
                    state=dict(STATE),
                    appraisal={"valence": 0.0, "arousal": 0.5, "threat": 0.1, "social": 0.3, "relevance": 0.6},
                    action=kind,
                    action_kind=kind,
                    domain="behavior",
                    modality="none",
                    outcome={"stress": 0.3, "social": 0.3, "mood": 0.0, "energy": 0.6, "identity": 0.7},
                    reward=1.0 if i == len(skills) - 1 else 0.0,
                    group=f"behavior:{task}:{file.stem}",
                )
            )
            previous.append(desc)
            if limit and len(out) >= limit:
                return out
    return out
