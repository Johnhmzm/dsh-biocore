from __future__ import annotations

from pathlib import Path
from typing import Tuple

from ..embedding import EmbeddingClient

DEFAULT = Path(__file__).resolve().parent.parent / "data" / "real" / "BEHAVIOR" / "behavior_policy.pt"


class BehaviorAdvice:
    """Trained BEHAVIOR MLP wrapped as MindBase(action_advice=...)."""

    def __init__(self, path: str | Path | None = None):
        import torch
        import torch.nn as nn

        self.torch = torch
        ckpt = torch.load(path or DEFAULT, map_location="cpu", weights_only=True)
        self.labels = list(ckpt["labels"])
        self.temperature = float(ckpt.get("temperature", 1.0))
        self.threshold = float(ckpt.get("threshold", 0.0))
        n_in = int(ckpt["n_in"])
        self.model = nn.Sequential(nn.Linear(n_in, 128), nn.ReLU(), nn.Dropout(0.2), nn.Linear(128, len(self.labels)))
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()
        self.client = EmbeddingClient.from_local_gguf()

    def advise(self, event: str, state=None) -> Tuple[str, float]:
        vec = self.client.embed([event])[0]
        with self.torch.no_grad():
            p = self.torch.softmax(self.model(self.torch.tensor([vec])) / self.temperature, dim=-1)[0]
        idx = int(p.argmax())
        return self.labels[idx], float(p[idx])
