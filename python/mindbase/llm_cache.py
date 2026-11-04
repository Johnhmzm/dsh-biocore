from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Dict

DEFAULT = Path(__file__).resolve().parent / "data" / "llm_cache.jsonl"


def key_of(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class LLMCache:
    """Disk cache for deterministic repeated LLM calls. Cache hits do not call the API."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or DEFAULT)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    self.data[str(row["key"])] = str(row["value"])
                except Exception:
                    continue

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def put(self, key: str, value: str) -> None:
        self.data[key] = value
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")


class CachedChat:
    def __init__(self, fn: Callable[..., str], path: Path | str | None = None):
        self.fn = fn
        self.cache = LLMCache(path)
        self.hits = 0
        self.misses = 0

    def __call__(self, *args, **kwargs) -> str:
        payload = {"args": args, "kwargs": kwargs}
        k = key_of(payload)
        hit = self.cache.get(k)
        if hit is not None:
            self.hits += 1
            return hit
        self.misses += 1
        value = self.fn(*args, **kwargs)
        self.cache.put(k, value)
        return value


class CachedLLMClient:
    def __init__(self, client, path: Path | str | None = None):
        self.client = client
        self.cache = LLMCache(path)
        self.hits = 0
        self.misses = 0

    def generate(self, request):
        payload = {"mode": request.mode, "context": request.context, "max_tokens": request.max_tokens}
        k = key_of(payload)
        hit = self.cache.get(k)
        if hit is not None:
            self.hits += 1
            return hit
        self.misses += 1
        value = self.client.generate(request)
        self.cache.put(k, value)
        return value
