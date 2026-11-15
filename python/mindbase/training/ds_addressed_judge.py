from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "addressed_to_agent.jsonl"
CACHE = Path(__file__).resolve().parent.parent / "data" / "ds_addressed_cache.json"
OUT = Path(__file__).resolve().parent.parent / "data" / "addressed_labels.jsonl"
LOCK = threading.Lock()

PROMPT = """她是群里的 AI 参与者“能天使”。当前这条消息明确提到了她/叫了她。
判断她此时该不该回应。只输出一行 JSON：
{"label":"speak" 或 "silence", "confidence":0.0-1.0, "reason":"一句话"}

规则：
- speak：直接问她、请求、邀请、需要她回答/接住情绪，且回应有增量；
- silence：只是第三人称提到、玩笑/口头提及、无需回应、骚扰/NSFW、引战、隐私、或她没有增量；
- 不确定 -> silence。

最近上下文：
{ctx}

当前消息：
{text}
"""


def key(s):
    return hashlib.sha256(s.encode()).hexdigest()


def ask(api_key, ctx, text):
    prompt = PROMPT.replace("{ctx}", ctx).replace("{text}", text)
    body = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": prompt}], "max_tokens": 512, "temperature": 0}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    raw = str(data["choices"][0]["message"]["content"] or "")
    try:
        start = raw.find("{")
        obj = json.loads(raw[start:raw.rfind("}") + 1]) if start >= 0 else {}
    except Exception:
        obj = {}
    label = str(obj.get("label", "")).lower()
    if label not in ("speak", "silence"):
        label = "speak" if "speak" in raw.lower() else "silence"
    return {"label": label, "confidence": obj.get("confidence"), "reason": str(obj.get("reason", ""))[:100]}


def run(n_threads=8):
    api_key = os.getenv("DEEPSEEK_API_KEY") or ""
    if not api_key: raise RuntimeError("set DEEPSEEK_API_KEY")
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = []
    for r in rows:
        k = key("\n".join(r["context"]) + "\n" + r["text"])
        if k not in cache:
            todo.append((k, r))
    def worker(item):
        k, r = item
        try:
            out = ask(api_key, "\n".join(r["context"]), r["text"])
        except Exception as e:
            out = {"label": "silence", "confidence": None, "reason": "ERR " + str(e)[:60]}
        with LOCK:
            cache[k] = out
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        list(ex.map(worker, todo))
    labels = []
    for r in rows:
        k = key("\n".join(r["context"]) + "\n" + r["text"])
        out = cache.get(k, {"label": "silence", "confidence": None, "reason": "missing"})
        labels.append({**r, **out})
    with OUT.open("w", encoding="utf-8") as fh:
        for r in labels:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    return {"n": len(labels), "labels": dict(Counter(x["label"] for x in labels)), "todo": len(todo), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
