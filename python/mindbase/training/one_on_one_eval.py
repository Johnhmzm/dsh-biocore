from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

from ..embedding import EmbeddingClient, cosine
from ..llm_openai import OpenAICompatClient
from ..one_on_one import OneOnOneSession, TRIGGERS

OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_eval.jsonl"


def run(limit: int = 249) -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    if not key: raise RuntimeError("set DEEPSEEK_API_KEY")
    rows = [json.loads(x) for x in TRIGGERS.read_text(encoding="utf-8").splitlines() if x.strip()][:limit]
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7)
    emb = EmbeddingClient.from_local_gguf(); emb.timeout = 120
    texts = []
    out = []
    for i, r in enumerate(rows):
        s = OneOnOneSession(seed=1000 + i, client=client, log_path=OUT.with_suffix(".log.jsonl"))
        res = s.step(r["user_text"])
        reply = str(res.get("reply") or "")
        actual = str(r.get("bot_reply") or "")
        texts += [reply, actual]
        out.append({"id": r["id"], "user_text": r["user_text"], "reply": reply, "actual": actual, "action": res.get("action"), "act": res.get("act"), "group": r["group"]})
        if i % 20 == 0:
            print("turn", i + 1, "/", len(rows), flush=True)
    vecs = emb.embed(texts)
    sims = []
    for i, r in enumerate(out):
        r["cosine"] = round(cosine(vecs[2 * i], vecs[2 * i + 1]), 4)
        sims.append(r["cosine"])
    with OUT.open("w", encoding="utf-8") as fh:
        for r in out: fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"n": len(out), "mean_cosine": round(statistics.mean(sims), 4), "median_cosine": round(statistics.median(sims), 4), "empty": sum(1 for r in out if not r["reply"]), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
