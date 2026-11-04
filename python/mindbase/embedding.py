from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from pathlib import Path
from typing import Dict, List, Sequence


class EmbeddingClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "EmbeddingClient":
        return cls(
            os.environ.get("EMBEDDING_BASE_URL", ""),
            os.environ.get("EMBEDDING_API_KEY", ""),
            os.environ.get("EMBEDDING_MODEL", ""),
        )

    @classmethod
    def from_local_gguf(cls, port: int = 8082, model: str = "qwen3-embed") -> "EmbeddingClient":
        return cls(base_url=f"http://127.0.0.1:{port}/v1", api_key="", model=model)

    @classmethod
    def from_astrbot(cls, path: str | Path | None = None, provider_id: str = "A-qwen3") -> "EmbeddingClient":
        path = Path(path or Path.home() / ".astrbot_launcher" / "instances")
        files = sorted(path.rglob("cmd_config.json")) if path.is_dir() else [path]
        for file in files:
            try:
                data = json.loads(file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            for item in data.get("provider", []):
                if item.get("id") == provider_id or item.get("provider_type") == "embedding":
                    if item.get("embedding_api_key"):
                        return cls(item["embedding_api_base"], item["embedding_api_key"], item["embedding_model"])
        raise RuntimeError("embedding provider not found in AstrBot config")

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        out: List[List[float]] = []
        for i in range(0, len(texts), 8):
            batch = list(texts[i:i + 8])
            body = json.dumps({"model": self.model, "input": batch}).encode("utf-8")
            req = urllib.request.Request(
                self.base_url + "/embeddings",
                data=body,
                headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out.extend(row["embedding"] for row in data["data"])
        return out


class CachedEmbedder:
    def __init__(self, client: "EmbeddingClient", path: str | Path):
        self.client = client
        self.path = Path(path)
        self.cache: Dict[str, List[float]] = {}
        if self.path.exists():
            try:
                self.cache = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _key(self, text: str) -> str:
        return hashlib.sha256((self.client.model + "\0" + text).encode("utf-8")).hexdigest()

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        missing = [t for t in texts if self._key(t) not in self.cache]
        if missing:
            for text, vec in zip(missing, self.client.embed(missing)):
                self.cache[self._key(text)] = vec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cache), encoding="utf-8")
        return [self.cache[self._key(t)] for t in texts]


def chunk_text(text: str, max_chars: int = 600, overlap: int = 80) -> List[str]:
    text = text or ""
    if len(text) <= max_chars:
        return [text]
    step = max(1, max_chars - overlap)
    return [text[i:i + max_chars] for i in range(0, len(text), step)]


class LocalEmbeddingClient:
    """Local transformers embedding; CLS pooling, L2 normalized. No API."""

    def __init__(self, model_path: str | Path, max_length: int = 512):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.model_path = str(model_path)
        self.model = self.model_path
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        self.net = AutoModel.from_pretrained(self.model_path)
        self.net.eval()
        self.max_length = max_length

    @classmethod
    def from_default(cls) -> "LocalEmbeddingClient":
        root = Path(__file__).resolve().parent.parent / "local_llm" / "v10" / "out-bge-small"
        return cls(root)

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        out: List[List[float]] = []
        for i in range(0, len(texts), 32):
            batch = list(texts[i:i + 32])
            enc = self.tokenizer(batch, padding=True, truncation=True, max_length=self.max_length, return_tensors="pt")
            with self.torch.no_grad():
                hidden = self.net(**enc).last_hidden_state
            vecs = self.torch.nn.functional.normalize(hidden[:, 0], dim=-1)
            out.extend(vecs.cpu().tolist())
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (denom or 1e-9)
