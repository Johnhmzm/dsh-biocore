from __future__ import annotations

import json
import math
import urllib.request
from typing import Dict, List, Optional, Sequence

from .memory import MemoryItem, jaccard


class EmbeddingClient:
    def __init__(self, base_url: str, api_key: str, model: str, dimensions: int = 1024, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.timeout = timeout
        self.cache: Dict[str, List[float]] = {}

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        todo = []
        for text in texts:
            if text not in self.cache:
                todo.append(text)
        if todo:
            payload = {"model": self.model, "input": list(todo)}
            req = urllib.request.Request(
                self.base_url + "/embeddings",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for text, item in zip(todo, data.get("data", [])):
                vec = item.get("embedding") or []
                if vec:
                    self.cache[text] = [float(x) for x in vec]
        return [self.cache.get(t, []) for t in texts]


class EmbeddingSearch:
    """Hybrid: cosine embedding + lexical overlap. Caches vectors per process."""

    def __init__(self, client: EmbeddingClient):
        self.client = client

    def search(self, query: str, items: List[MemoryItem], k: int = 6) -> List[MemoryItem]:
        if not items:
            return []
        texts = [str(x.text or "") for x in items]
        try:
            vectors = self.client.embed([query] + texts)
        except Exception:
            vectors = []
        qv = vectors[0] if vectors else []
        if not qv:
            qgrams = {str(query)[i:i + 2] for i in range(max(0, len(str(query)) - 1))}
            scored = [(jaccard(item.bigrams, qgrams), item) for item in items]
            scored.sort(key=lambda x: x[0], reverse=True)
            return [x[1] for x in scored[:k]]
        qn = math.sqrt(sum(x * x for x in qv)) or 1.0
        scored = []
        qgrams = {query[i:i + 2] for i in range(max(0, len(query) - 1))}
        for item, vec in zip(items, vectors[1:]):
            if not vec:
                continue
            vn = math.sqrt(sum(x * x for x in vec)) or 1.0
            cos = sum(a * b for a, b in zip(qv, vec)) / (qn * vn)
            lexical = jaccard(item.bigrams, qgrams)
            score = 0.7 * cos + 0.3 * lexical
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored[:k]]
