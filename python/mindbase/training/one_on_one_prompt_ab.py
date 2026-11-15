from __future__ import annotations

import json
import os
import statistics
import urllib.request
from pathlib import Path

from mindbase.embedding import EmbeddingClient, cosine
from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession, TRIGGERS

OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_prompt_ab.jsonl"
PROMPT = (
    "你是她的表达层。用中文，短句，先接住情绪再回应；"
    "不要客服腔，不要每句都提问，不要解释规则。只输出对用户说的一句话。"
)


def run(limit: int = 249) -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    rows = [json.loads(x) for x in TRIGGERS.read_text(encoding="utf-8").splitlines() if x.strip()][:limit]
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7, character_prompt=PROMPT)
    emb = EmbeddingClient.from_local_gguf(); emb.timeout = 120
    texts, out = [], []
    for i, r in enumerate(rows):
        s = OneOnOneSession(seed=2000 + i, client=client, log_path=OUT.with_suffix(".log.jsonl"))
        res = s.step(r["user_text"])
        reply = str(res.get("reply") or "")
        actual = str(r.get("bot_reply") or "")
        texts += [reply, actual]
        out.append({"id": r["id"], "reply": reply, "actual": actual})
        if i % 20 == 0: print("turn", i + 1, flush=True)
    vecs = emb.embed(texts)
    sims = [cosine(vecs[2*i], vecs[2*i+1]) for i in range(len(out))]
    for i, r in enumerate(out): r["cosine"] = round(sims[i], 4)
    with OUT.open("w", encoding="utf-8") as fh:
        for r in out: fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"n": len(out), "mean_cosine": round(statistics.mean(sims), 4), "median_cosine": round(statistics.median(sims), 4), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
