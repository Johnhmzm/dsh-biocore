"""SiliconFlow embedding + rerank, reusing AstrBot provider config.

Reads the AstrBot instance config (no secrets are logged) and exposes:
- embed(texts) -> vectors
- rerank(query, documents, top_n) -> [(index, score)]
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_CONFIG = Path(r"C:\Users\Administrator\.astrbot_launcher\instances\4200c1fe-c355-4eed-bed3-30878989b5ad\core\data\cmd_config.json")


def _first(value):
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


class EmbeddingRerankClient:
    def __init__(self, config_path: Optional[Path | str] = None):
        self.embed_enabled = False
        self.rerank_enabled = False
        self.embed_base = ""
        self.embed_model = ""
        self.embed_key = ""
        self.embed_dim = 0
        self.rerank_url = ""
        self.rerank_model = ""
        self.rerank_key = ""
        self.last_error = ""
        self._load(config_path or os.getenv("ASTRBOT_CONFIG") or DEFAULT_CONFIG)

    def _load(self, path: Path | str) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except Exception as exc:
            self.last_error = type(exc).__name__
            return
        for p in data.get("provider", []):
            ptype = str(p.get("provider_type") or "")
            if ptype == "embedding" and p.get("enable", True):
                self.embed_base = str(p.get("embedding_api_base") or "").rstrip("/")
                self.embed_model = str(p.get("embedding_model") or "")
                self.embed_key = _first(p.get("embedding_api_key"))
                try:
                    self.embed_dim = int(p.get("embedding_dimensions") or 0)
                except Exception:
                    self.embed_dim = 0
                self.embed_enabled = bool(self.embed_base and self.embed_model and self.embed_key)
            elif ptype == "rerank" and p.get("enable", True):
                base = str(p.get("rerank_api_base") or "").rstrip("/")
                suffix = str(p.get("rerank_api_suffix") or "/v1/rerank")
                self.rerank_url = base + suffix
                self.rerank_model = str(p.get("rerank_model") or "")
                self.rerank_key = _first(p.get("rerank_api_key"))
                self.rerank_enabled = bool(self.rerank_url and self.rerank_model and self.rerank_key)

    @property
    def enabled(self) -> bool:
        return self.embed_enabled or self.rerank_enabled

    def embed(self, texts: Sequence[str], timeout: float = 20.0) -> List[List[float]]:
        if not self.embed_enabled:
            raise RuntimeError("embedding provider disabled")
        payload = {"model": self.embed_model, "input": [str(t) for t in texts]}
        req = urllib.request.Request(
            self.embed_base + "/embeddings",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.embed_key,
                     "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.last_error = "embed HTTP %s" % exc.code
            raise RuntimeError(self.last_error) from exc
        except Exception as exc:
            self.last_error = "embed " + type(exc).__name__
            raise
        out = []
        for item in data.get("data") or []:
            vec = [float(x) for x in (item.get("embedding") or [])]
            if not vec or not all(math.isfinite(x) for x in vec):
                raise RuntimeError("invalid embedding vector")
            if self.embed_dim and len(vec) != self.embed_dim:
                raise RuntimeError("embedding dimension mismatch: %s != %s" % (len(vec), self.embed_dim))
            out.append(vec)
        return out

    def rerank(self, query: str, documents: Sequence[str], top_n: int = 10,
               timeout: float = 20.0) -> List[Tuple[int, float]]:
        if not self.rerank_enabled:
            raise RuntimeError("rerank provider disabled")
        payload = {"model": self.rerank_model, "query": str(query),
                   "documents": [str(d) for d in documents], "top_n": int(top_n)}
        req = urllib.request.Request(
            self.rerank_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.rerank_key,
                     "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.last_error = "rerank HTTP %s" % exc.code
            raise RuntimeError(self.last_error) from exc
        except Exception as exc:
            self.last_error = "rerank " + type(exc).__name__
            raise
        results = data.get("results") or data.get("data") or []
        out = []
        for item in results:
            try:
                out.append((int(item.get("index")), float(item.get("relevance_score", item.get("score", 0.0)))))
            except Exception:
                continue
        out.sort(key=lambda x: x[1], reverse=True)
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    return dot / (na * nb + 1e-9)


_RAG: Optional[EmbeddingRerankClient] = None


def get_rag() -> EmbeddingRerankClient:
    global _RAG
    if _RAG is None:
        _RAG = EmbeddingRerankClient()
    return _RAG
