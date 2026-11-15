from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession
from mindbase.training.auto_eval import ask

DATA = Path(__file__).resolve().parent.parent / "data" / "one_on_one_auto_eval.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "empathy_ab.jsonl"
PROMPT = (
    "你是她的表达层。先接住用户的情绪，再回应；不要先讲道理或给建议；"
    "用一句具体的感受，短、自然、像朋友。只输出对用户说的一句话。"
)


def run(n: int = 30):
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    low = [r for r in rows if float(r.get("empathy", 5)) <= 3][:n]
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7, character_prompt=PROMPT)
    out = []
    for i, r in enumerate(low):
        s = OneOnOneSession(seed=3000 + i, client=client, log_path=OUT.with_suffix(".log.jsonl"))
        res = s.step(str(r["user_text"]))
        reply = str(res.get("reply") or "")
        judge = ask(key, str(r["user_text"]), reply)
        out.append({"id": r["id"], "reply": reply, "baseline": {k: r.get(k) for k in ("relevance", "natural", "empathy", "persona", "length", "rating")}, "candidate": judge})
    with OUT.open("w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    def mean(key_):
        return round(statistics.mean(float(r["candidate"].get(key_, 3)) for r in out), 3)
    return {"n": len(out), "candidate_means": {k: mean(k) for k in ("relevance", "natural", "empathy", "persona", "length")}, "baseline_empathy": round(statistics.mean(float(r["baseline"].get("empathy") or 3) for r in out), 3), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
