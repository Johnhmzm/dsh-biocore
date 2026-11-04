from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import List, Sequence


class RerankClient:
    def __init__(self, base_url: str, api_key: str, model: str, suffix: str = "/v1/rerank", timeout: float = 30.0):
        self.url = base_url.rstrip("/") + suffix
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_astrbot(cls, path: str | Path | None = None, provider_id: str = "siliconflow-rerank") -> "RerankClient":
        root = Path(path or Path.home() / ".astrbot_launcher" / "instances")
        files = sorted(root.rglob("cmd_config.json")) if root.is_dir() else [root]
        for file in files:
            try:
                data = json.loads(file.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            for item in data.get("provider", []):
                if item.get("id") == provider_id or item.get("provider_type") == "rerank":
                    if item.get("rerank_api_key"):
                        return cls(item["rerank_api_base"], item["rerank_api_key"], item["rerank_model"], item.get("rerank_api_suffix", "/v1/rerank"))
        raise RuntimeError("rerank provider not found")

    def rerank(self, query: str, documents: Sequence[str], top_n: int = 5) -> List[int]:
        body = json.dumps({"model": self.model, "query": query, "documents": list(documents), "top_n": top_n}).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = sorted(data.get("results", []), key=lambda x: x.get("relevance_score", 0), reverse=True)
        return [int(x["index"]) for x in rows if "index" in x]
