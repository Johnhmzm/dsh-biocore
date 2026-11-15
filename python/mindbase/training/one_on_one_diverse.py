from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession

OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_diverse.jsonl"
PERSONAS = [
    ("tired", "你是一个 28 岁上班族，今天很累，想随便聊聊。短句、口语；有时只回一两个字。", "今天有点累"),
    ("lonely", "你最近很孤独，想找人说话，但又怕打扰别人。短句、有点犹豫。", "在吗，你会不会觉得我很烦"),
    ("angry", "你刚被朋友背叛，情绪很差，说话带刺，不太信任她。短句、直接。", "你会不会也骗我"),
    ("playful", "你喜欢开玩笑，语气轻松，偶尔故意逗她。短句。", "哈哈你在干嘛呢"),
    ("resistant", "你不太想聊，觉得没意思，回答很短，偶尔说别问了。", "随便吧，别问太多"),
]


def user_chat(api_key, messages):
    body = json.dumps({"model": "deepseek-flash", "messages": messages, "max_tokens": 512, "temperature": 0.95}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    return str(data["choices"][0]["message"]["content"] or "").strip()


def main(turns_per: int = 15) -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    if not key: raise RuntimeError("set DEEPSEEK_API_KEY")
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.7)
    all_examples = []
    for pi, (name, persona, opener) in enumerate(PERSONAS):
        s = OneOnOneSession(seed=9100 + pi, client=client, log_path=OUT, metadata={"simulated": True, "feedback_valid": False, "persona": name, "source": "ds_user_sim_diverse"})
        messages = [{"role": "system", "content": persona}, {"role": "user", "content": "开始对话，你先说第一句。"}]
        try:
            user_text = user_chat(key, messages).strip() or opener
        except Exception:
            user_text = opener
        for ti in range(turns_per):
            r = s.step(user_text, user_response=None)
            reply = r.get("reply") or ""
            if ti < 3:
                all_examples.append({"persona": name, "user": user_text, "reply": reply})
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": "继续用一句用户的话回复她。只输出这句话。"})
            try:
                user_text = user_chat(key, messages).strip() or "嗯"
            except Exception:
                user_text = "嗯"
            messages = messages[-12:]
        print(name, "done", flush=True)
    return {"turns": len(PERSONAS) * turns_per, "personas": [p[0] for p in PERSONAS], "path": str(OUT), "examples": all_examples}


if __name__ == "__main__":
    r = main()
    print({"turns": r["turns"], "path": r["path"]})
    for x in r["examples"][:6]: print(x)
