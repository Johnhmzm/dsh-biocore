from __future__ import annotations

import json
import os
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession, TRIGGERS
from mindbase.training.auto_eval import ask

OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_eval_v2.jsonl"
LOCK = threading.Lock()


def run(n_threads: int = 6):
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    rows = [json.loads(x) for x in TRIGGERS.read_text(encoding="utf-8").splitlines() if x.strip()]
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7)
    out = []
    def work(item):
        i, r = item
        try:
            s = OneOnOneSession(seed=4000 + i, client=client, log_path=OUT.with_suffix(".log.jsonl"))
            rep = str(s.step(str(r["user_text"])).get("reply") or "")
            judge = ask(key, str(r["user_text"]), rep)
            row = {"id": r["id"], "user_text": r["user_text"], "reply": rep, **judge}
        except Exception as e:
            row = {"id": r["id"], "user_text": r["user_text"], "reply": "", "rating": "bad", "comment": "ERR" + str(e)[:40]}
        with LOCK: out.append(row)
    with ThreadPoolExecutor(max_workers=n_threads) as ex: list(ex.map(work, enumerate(rows)))
    out.sort(key=lambda x: x["id"])
    with OUT.open("w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    dims = ["relevance", "natural", "empathy", "persona", "length"]
    means = {d: round(statistics.mean(float(r.get(d, 3)) for r in out), 3) for d in dims}
    ratings = {k: sum(1 for r in out if r.get("rating") == k) for k in ("good", "mid", "bad")}
    return {"n": len(out), "means": means, "ratings": ratings, "silent": sum(1 for r in out if r.get("silent")), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
