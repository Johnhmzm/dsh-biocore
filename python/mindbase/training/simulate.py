from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Dict, List

from ..config import MindConfig
from ..engine import MindBase
from ..interaction_log import InteractionLogger
from ..llm_adapter import LLMAdapter
from ..llm_openai import OpenAICompatClient
from ..llm_cache import CachedChat, CachedLLMClient
from ..types import Event

OUT = Path(__file__).resolve().parent.parent / "data" / "simulated_interactions.jsonl"


def chat(api_key: str, messages: List[Dict[str, str]], model: str = "deepseek-flash") -> str:
    for attempt in range(2):
        body = json.dumps(
            {"model": model, "messages": messages, "max_tokens": 512, "temperature": 0.9 if attempt == 0 else 0.7}
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=body,
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = str(data["choices"][0]["message"]["content"] or "").strip()
        if text:
            return text
    return ""


def parse_user(text: str) -> tuple[bool, str]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").replace("json", "", 1).strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "text" in obj:
            return bool(obj.get("directed", False)), str(obj["text"]).strip()
    except Exception:
        pass
    return False, raw



def simulate(episodes: int = 2, turns: int = 5, seed: int = 42) -> dict:
    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or ""
    if not key:
        raise RuntimeError("set DEEPSEEK_API_KEY")
    client = OpenAICompatClient(model="deepseek-flash", base_url="https://api.deepseek.com", api_key=key, temperature=0.8)
    cached_chat = CachedChat(lambda messages: chat(key, messages))
    log = InteractionLogger(OUT)
    total = 0
    for ep in range(episodes):
        mb = MindBase(MindConfig(seed=seed + ep))
        adapter = LLMAdapter(mb, CachedLLMClient(client))
        persona = (
            "你是一个有点累、但愿意聊天的普通用户。你有时直接对她说，有时只是自言自语或继续自己的事，不问她。"
            "每次只输出一行 JSON: {\"directed\": true/false, \"text\": \"一句话\"}。"
            "directed=true 表示你在对她说/问她；false 表示你只是在旁白或自言自语。"
        )
        messages = [{"role": "system", "content": persona}]
        opener = "你好，在吗？"
        messages.append({"role": "user", "content": opener})
        user_text = opener
        directed = True
        for turn in range(turns):
            state_before = mb.snapshot()
            out = adapter.handle(Event(text=user_text, speaker="A", directed=directed, ts=mb.t + 60), feedback=0.0)
            reply = out.reply
            state_after = mb.snapshot()
            # user simulator replies to the base
            if reply:
                messages.append({"role": "assistant", "content": reply})
            try:
                raw = cached_chat(messages)
            except Exception:
                raw = ""
            next_directed, nxt = parse_user(raw)
            if not nxt:
                nxt = "嗯，我先不说了。"
                next_directed = False
            messages.append({"role": "user", "content": nxt})
            log.record(
                {
                    "simulated": True,
                    "feedback_valid": False,
                    "source": "deepseek",
                    "episode": ep,
                    "turn": turn,
                    "state_before": state_before,
                    "event": user_text,
                    "action": out.outcome.action,
                    "reason": out.outcome.reason,
                    "llm_request": bool(out.outcome.llm_request),
                    "state_after": state_after,
                    "reply": reply,
                    "user_response": nxt,
                }
            )
            total += 1
            user_text = nxt
            directed = next_directed
    return {"episodes": episodes, "turns": total, "path": str(OUT), "cache_hits": cached_chat.hits, "cache_misses": cached_chat.misses}


if __name__ == "__main__":
    print(simulate())
