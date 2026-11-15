from __future__ import annotations

import json
import os
import random
import urllib.request
from pathlib import Path
from typing import Dict, List

import pandas as pd

from ..llm_cache import LLMCache

CPED = Path(__file__).resolve().parent.parent / "data" / "real" / "CPED" / "valid_split.csv"
OUT = Path(__file__).resolve().parent.parent / "data" / "ds_gate_sample.jsonl"


def sample(n: int = 20, seed: int = 0) -> List[Dict[str, object]]:
    df = pd.read_csv(CPED).dropna(subset=["Dialogue_ID", "Utterance_ID", "Utterance", "Speaker"])
    rows = []
    for did, g in df.groupby("Dialogue_ID"):
        g = g.sort_values("Utterance_ID")
        texts = [str(x) for x in g["Utterance"]]
        speakers = [str(x) for x in g["Speaker"]]
        if len(texts) < 4:
            continue
        target = speakers[0]
        for i in range(2, len(texts) - 1):
            context = "\n".join(f"{speakers[j]}: {texts[j]}" for j in range(max(0, i - 3), i + 1))
            rows.append({"id": f"{did}:{i}", "context": context, "target": target, "human": 1 if speakers[i + 1] == target else 0})
    random.Random(seed).shuffle(rows)
    return rows[:n]


def ds_label(key: str, context: str, target: str) -> int | None:
    prompt = (
        "判断下一轮该不该由 " + target + " 说话。只输出 JSON：{\"label\": \"speak\"} 或 {\"label\": \"silence\"}。\n\n"
        + context
    )
    body = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": prompt}], "max_tokens": 256, "temperature": 0}).encode("utf-8")
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = str(data["choices"][0]["message"]["content"] or "")
    if "silence" in text: return 0
    if "speak" in text: return 1
    return None


def run(n: int = 20) -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    if not key: raise RuntimeError("set DEEPSEEK_API_KEY")
    cache = LLMCache()
    rows = sample(n)
    out = []
    for r in rows:
        k = "ds_label:" + r["id"]
        hit = cache.get(k)
        if hit is not None:
            label = int(hit)
        else:
            label = ds_label(key, str(r["context"]), str(r["target"]))
            if label is not None: cache.put(k, str(label))
        out.append({**r, "ds": label, "processed_by": "ds", "label_source": "ds", "synthetic": True})
    with OUT.open("w", encoding="utf-8") as fh:
        for r in out: fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    pairs = [(r["human"], r["ds"]) for r in out if r["ds"] is not None]
    agree = sum(1 for h, d in pairs if h == d) / max(1, len(pairs))
    return {"n": len(out), "labeled": len(pairs), "agreement": round(agree, 3), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
