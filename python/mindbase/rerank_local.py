"""Local ONNX reranker (jina-reranker-v2 int8).

Cross-encoder query-document relevance score. GPU via onnxruntime CUDA provider;
falls back to CPU. This module only scores; it does not decide to reply.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np


class LocalReranker:
    def __init__(self, model_dir: str | Path, max_length: int = 256,
                 providers: list[str] | None = None):
        import onnxruntime as ort
        from transformers import AutoTokenizer
        self.dir = Path(model_dir)
        self.max_length = int(max_length)
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.dir))
        onnx_path = self.dir / "onnx" / "model_int8.onnx"
        if not onnx_path.exists():
            onnx_path = self.dir / "model_int8.onnx"
        self.session = ort.InferenceSession(
            str(onnx_path),
            providers=providers or ["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.provider = self.session.get_providers()[0] if self.session.get_providers() else "CPU"
        self.last_latency = 0.0

    def score(self, query: str, docs: list[str]) -> list[float]:
        pairs = [(str(query), str(d)) for d in docs]
        enc = self.tokenizer([p[0] for p in pairs], [p[1] for p in pairs],
                             padding=True, truncation=True,
                             max_length=self.max_length, return_tensors="np")
        feeds = {k: v for k, v in enc.items() if k in ("input_ids", "attention_mask", "token_type_ids")}
        t0 = time.time()
        out = self.session.run(None, feeds)[0]
        self.last_latency = time.time() - t0
        logits = np.asarray(out).reshape(len(docs), -1)[:, 0]
        return (1.0 / (1.0 + np.exp(-logits))).astype(float).tolist()

    def info(self) -> dict:
        return {"provider": self.provider, "max_length": self.max_length,
                "last_latency": round(self.last_latency, 3), "dir": str(self.dir)}
