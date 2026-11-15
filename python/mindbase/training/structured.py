from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .alfworld import action_kind
from .dataset import StorySample

STATE = {"stress": 0.3, "social": 0.3, "mood": 0.0, "energy": 0.6, "identity": 0.7}


@dataclass
class StructuredTrajectory:
    task: str
    observations: List[str]
    actions: List[str]
    rewards: List[float]
    dones: List[bool]

    def __post_init__(self):
        n = len(self.actions)
        self.observations = list(self.observations) + [""] * max(0, n - len(self.observations))
        self.rewards = list(self.rewards) + [0.0] * max(0, n - len(self.rewards))
        self.dones = list(self.dones) + [False] * max(0, n - len(self.dones))


def load_jsonl(path: Path | str) -> List[StructuredTrajectory]:
    out: List[StructuredTrajectory] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "actions" not in row:
            continue
        out.append(
            StructuredTrajectory(
                task=str(row.get("task") or row.get("task_type") or "task"),
                observations=[str(x) for x in row.get("observations", [])],
                actions=[str(x) for x in row.get("actions", [])],
                rewards=[float(x) for x in row.get("rewards", [])],
                dones=[bool(x) for x in row.get("dones", [])],
            )
        )
    return out


def load_hdf5(path: Path | str) -> List[StructuredTrajectory]:
    try:
        import h5py  # type: ignore
    except Exception as e:
        raise RuntimeError("h5py is required for BEHAVIOR/ENACT HDF5") from e
    out: List[StructuredTrajectory] = []
    with h5py.File(path, "r") as fh:
        for key in fh.keys():
            g = fh[key]
            out.append(
                StructuredTrajectory(
                    task=str(key),
                    observations=[x.decode() if isinstance(x, bytes) else str(x) for x in g.get("observations", [])],
                    actions=[x.decode() if isinstance(x, bytes) else str(x) for x in g.get("actions", [])],
                    rewards=[float(x) for x in g.get("rewards", [])],
                    dones=[bool(x) for x in g.get("dones", [])],
                )
            )
    return out


def to_samples(trajectories: Sequence[StructuredTrajectory]) -> List[StorySample]:
    out: List[StorySample] = []
    for ti, traj in enumerate(trajectories):
        for i, action in enumerate(traj.actions):
            reward = traj.rewards[i] if i < len(traj.rewards) else 0.0
            kind = action_kind(action)
            out.append(
                StorySample(
                    event=traj.observations[i] if i < len(traj.observations) else "",
                    state=dict(STATE),
                    appraisal={"valence": 0.0, "arousal": 0.5, "threat": 0.1, "social": 0.3, "relevance": 0.6},
                    action=kind,
                    action_kind=kind,
                    domain="structured",
                    modality="none",
                    outcome={"stress": max(0.0, 0.3 - 0.2 * reward), "social": 0.3, "mood": 0.1 * reward, "energy": 0.6, "identity": 0.7},
                    reward=reward,
                    group=f"traj:{ti}:{traj.task}",
                )
            )
    return out
