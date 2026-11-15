from __future__ import annotations

import json
import os
from pathlib import Path

from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession
from mindbase.training.auto_eval import ask

DATA = Path(__file__).resolve().parent.parent / "data" / "one_on_one_eval_v2.jsonl"


def main() -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7)
    fixed = 0
    for i, r in enumerate(rows):
        if r.get("rating") != "bad":
            continue
        best = None
        for attempt in range(3):
            s = OneOnOneSession(seed=5000 + i * 3 + attempt, client=client, log_path=DATA.with_suffix(".repair.log.jsonl"))
            reply = str(s.step(str(r["user_text"])).get("reply") or "").strip()
            if len(reply) >= 4:
                best = reply
                break
        if not best:
            continue
        judge = ask(key, str(r["user_text"]), best)
        rows[i] = {**r, "reply": best, **judge}
        fixed += 1
    with DATA.open("w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"fixed": fixed, "remaining_bad": sum(1 for r in rows if r.get("rating") == "bad")}


if __name__ == "__main__":
    print(main())
