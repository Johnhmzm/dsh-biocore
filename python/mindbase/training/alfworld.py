from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from ..actions import ACTION_KINDS
from .dataset import StorySample

STATE = {"stress": 0.3, "social": 0.3, "mood": 0.0, "energy": 0.6, "identity": 0.7}


def action_kind(action: str) -> str:
    a = (action or "").lower()
    if a.startswith(("go to", "navigate")):
        return "approach"
    if a.startswith(("find", "look", "search")):
        return "explore"
    if a.startswith("take") or a.startswith("pick"):
        return "approach"
    if a.startswith("put") or a.startswith("place"):
        return "give_help"
    if a.startswith("open"):
        return "explore"
    if a.startswith("close"):
        return "avoid"
    if a.startswith(("clean", "heat", "cool", "wash")):
        return "approach"
    if a.startswith(("look", "inventory", "examine")):
        return "appraise"
    if a.startswith(("tell", "say", "ask")):
        return "communicate"
    return "communicate"


def _extract_action(text: str) -> str:
    matches = re.findall(r"Action:\s*([^\n]+)", text or "")
    return matches[-1].strip() if matches else ""


def load_alfworld_sft(path: Path | str, limit: int | None = None, history: bool = False) -> List[StorySample]:
    """ALFWorld SFT JSONL: messages with Thought/Action; real trajectories."""
    out: List[StorySample] = []
    previous_action = ""
    with Path(path).open("r", encoding="utf-8", errors="ignore") as fh:
        for row_index, line in enumerate(fh):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            messages = row.get("messages") or []
            last_user = ""
            for msg in messages:
                role = msg.get("role")
                content = str(msg.get("content") or "")
                if role == "user":
                    last_user = content
                elif role == "assistant":
                    action = _extract_action(content)
                    if not action:
                        continue
                    kind = action_kind(action)
                    event = last_user[-2000:]
                    if history and previous_action:
                        event = f"previous action: {previous_action}\n{event}"
                    out.append(
                        StorySample(
                            event=event,
                            state=dict(STATE),
                            appraisal={"valence": 0.0, "arousal": 0.5, "threat": 0.1, "social": 0.3, "relevance": 0.6},
                            action=kind,
                            action_kind=kind,
                            domain="embodied",
                            modality="none",
                            outcome={"stress": 0.3, "social": 0.3, "mood": 0.0, "energy": 0.6, "identity": 0.7},
                            reward=0.0,
                            group=f"alfworld_sft:{row.get('file_name') or row_index}",
                        )
                    )
                    previous_action = action
                    if limit and len(out) >= limit:
                        return out
    return out


def load_trajectories(path: Path | str) -> List[StorySample]:
    path = Path(path)
    rows: List[dict] = []
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("trajectories", [])
    out: List[StorySample] = []
    for task_index, row in enumerate(rows):
        steps = row.get("steps") or []
        task = str(row.get("task") or f"task-{task_index}")
        for i, step in enumerate(steps):
            kind = action_kind(str(step.get("action") or ""))
            reward = float(step.get("reward") or 0.0)
            nxt = steps[i + 1].get("obs", "") if i + 1 < len(steps) else ""
            out.append(
                StorySample(
                    event=str(step.get("obs") or ""),
                    state=dict(STATE),
                    appraisal={"valence": 0.0, "arousal": 0.5, "threat": 0.1, "social": 0.3, "relevance": 0.6},
                    action=kind,
                    action_kind=kind,
                    domain="embodied",
                    modality="none",
                    outcome={"stress": max(0.0, 0.3 - 0.2 * reward), "social": 0.3, "mood": 0.1 * reward, "energy": 0.6, "identity": 0.7},
                    reward=reward,
                    group=f"alfworld:{task}",
                )
            )
    return out
