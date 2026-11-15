from __future__ import annotations

import json
import os
from pathlib import Path

from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession

OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_pilot.jsonl"
MESSAGES = [
    "你好", "在吗", "今天有点累", "刚下班", "老板今天骂我了", "我有点难受",
    "你会怎么安慰人", "我们算朋友吗", "你记得我刚才说什么吗", "你觉得我这个人怎么样",
    "我不想聊这个", "你能不能别问了", "你根本不懂我", "刚才是我不好", "你还生气吗",
    "嗯", "哦", "随便", "今天先这样", "明天再聊",
]


def main() -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    if not key: raise RuntimeError("set DEEPSEEK_API_KEY")
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7)
    s = OneOnOneSession(seed=4242, client=client, log_path=OUT, metadata={"simulated": True, "feedback_valid": False, "source": "pilot_script"})
    rows = []
    for i, text in enumerate(MESSAGES):
        nxt = MESSAGES[i + 1] if i + 1 < len(MESSAGES) else None
        r = s.step(text, user_response=nxt)
        rows.append({"event": text, "reply": r.get("reply"), "action": r.get("action"), "act": r.get("act")})
    return {"turns": len(rows), "path": str(OUT), "rows": rows}


if __name__ == "__main__":
    r = main()
    print({"turns": r["turns"], "path": r["path"]})
    for x in r["rows"][:5]:
        print(x)
