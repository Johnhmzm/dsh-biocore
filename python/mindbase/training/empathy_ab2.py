from __future__ import annotations
import json, os, statistics
from pathlib import Path
from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession
from mindbase.training.auto_eval import ask

DATA = Path(__file__).resolve().parent.parent / "data" / "one_on_one_eval_v2.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "empathy_ab2.jsonl"
PROMPT = (
    "你是她的表达层。先用一句具体的话接住用户的核心问题或情绪，再补一句；"
    "不要用反问代替回答，不要说“你想多了/急什么”来否定对方；"
    "不要先讲道理；短、自然、像朋友。只输出一句话。"
)

def run():
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    low = [r for r in rows if float(r.get("empathy") or 5) <= 2.5]
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7, character_prompt=PROMPT)
    out = []
    for i, r in enumerate(low):
        best = ""
        for a in range(3):
            s = OneOnOneSession(seed=6000+i*3+a, client=client, log_path=OUT.with_suffix(".log.jsonl"))
            best = str(s.step(str(r["user_text"])).get("reply") or "").strip()
            if len(best) >= 4: break
        j = ask(key, str(r["user_text"]), best)
        out.append({"id": r["id"], "reply": best, "baseline_empathy": r.get("empathy"), "candidate": j})
    with OUT.open("w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    dims = ["relevance","natural","empathy","persona","length"]
    return {"n": len(out), "baseline_empathy": round(statistics.mean(float(r["baseline_empathy"] or 3) for r in out),3), "candidate": {d: round(statistics.mean(float(r["candidate"].get(d) or 3) for r in out),3) for d in dims}}

if __name__ == "__main__": print(run())
