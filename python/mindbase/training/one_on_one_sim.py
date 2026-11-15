from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession

OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_simulated.jsonl"


def user_chat(api_key, messages):
    body = json.dumps({"model": "deepseek-flash", "messages": messages, "max_tokens": 512, "temperature": 0.9}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return str(data["choices"][0]["message"]["content"] or "").strip()


def main(turns: int = 40) -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    if not key: raise RuntimeError("set DEEPSEEK_API_KEY")
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7)
    s = OneOnOneSession(seed=9001, client=client, log_path=OUT, metadata={"simulated": True, "feedback_valid": False, "source": "ds_user_sim"})
    persona = (
        "你是一个 28 岁上班族，今天有点累，来找她聊天。短句、口语、真实；"
        "不要每轮都提问；有时冷淡、有时不想聊、有时只回一两个字。只说用户这一句。"
    )
    messages = [{"role": "system", "content": persona}, {"role": "user", "content": "开始对话，你先说第一句。"}]
    user_text = user_chat(key, messages).strip() or "在吗"
    examples = []
    for i in range(turns):
        r = s.step(user_text, user_response=None)
        reply = r.get("reply") or ""
        if i < 8:
            examples.append({"user": user_text, "reply": reply, "action": r.get("action")})
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": "继续用一句用户的话回复她。只输出这句话。"})
        try:
            user_text = user_chat(key, messages).strip() or "嗯"
        except Exception:
            user_text = "嗯"
        messages = messages[-12:]
    return {"turns": turns, "path": str(OUT), "examples": examples}


if __name__ == "__main__":
    r = main()
    print({"turns": r["turns"], "path": r["path"]})
    for x in r["examples"][:5]: print(x)
