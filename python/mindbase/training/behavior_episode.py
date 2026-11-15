from __future__ import annotations

import json
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

from ..noise import clip
from .dataset import StorySample

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR"


def _summary(vec) -> dict:
    x = np.asarray(vec, dtype=float).ravel()
    return {
        "stress": float(clip(0.3 + 0.1 * float(np.mean(x)), 0.0, 1.0)),
        "social": float(clip(0.5 + 0.1 * float(np.std(x)), 0.0, 1.0)),
        "mood": float(clip(float(np.mean(x)), -1.0, 1.0)),
        "energy": float(clip(0.5 + 0.2 * float(np.linalg.norm(x) / max(1.0, x.size ** 0.5)), 0.0, 1.0)),
        "identity": 0.7,
    }


def load_episode(parquet_path: Path | str, annotation_path: Path | str) -> List[StorySample]:
    df = pd.read_parquet(parquet_path)
    ann = json.loads(Path(annotation_path).read_text(encoding="utf-8"))
    skills = ann.get("skill_annotation") or []
    task = str(ann.get("task_name") or Path(annotation_path).stem)
    samples: List[StorySample] = []
    states = [np.asarray(v, dtype=float).ravel() for v in df["observation.state"]]
    actions = [np.asarray(v, dtype=float).ravel() for v in df["action"]]
    for i in range(len(df)):
        skill = next((s for s in skills if s.get("frame_duration") and s["frame_duration"][0] <= i < s["frame_duration"][1]), None)
        if skill is None:
            continue
        desc = " ".join(str(x) for x in (skill.get("skill_description") or []))
        kind = desc or "unknown"
        nxt = states[i + 1] if i + 1 < len(states) else states[i]
        samples.append(
            StorySample(
                event=f"{task}; {desc}; frame {i}",
                state=_summary(states[i]),
                appraisal={"valence": 0.0, "arousal": 0.5, "threat": 0.1, "social": 0.3, "relevance": 0.7},
                action=kind,
                action_kind=kind,
                domain="behavior",
                modality="none",
                outcome=_summary(nxt),
                reward=1.0 if i == len(df) - 1 else 0.0,
                group=f"behavior_episode:{ann.get('meta_data', {}).get('task_duration', 0)}:{Path(annotation_path).stem}",
            )
        )
    return samples


if __name__ == "__main__":
    p = DATA / "episode_00000010.parquet"
    a = DATA / "annotations" / "task-0000" / "episode_00000010.json"
    rows = load_episode(p, a)
    print(len(rows), rows[0].action_kind, rows[-1].action_kind)
