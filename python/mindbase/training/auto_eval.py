from __future__ import annotations

import hashlib
import json
import os
import statistics
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "one_on_one_eval.jsonl"
CACHE = Path(__file__).resolve().parent.parent / "data" / "auto_eval_cache.json"
OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_auto_eval.jsonl"
LOCK = threading.Lock()

PROMPT = """你是严格的对话评估员。只评估“她的回复”，不要参考实际回复。
用户说：
{user}

她回复：
{reply}

按 1-5 打分（5 最好）：
- relevance 相关：是否接住用户内容
- natural 自然：像不像真人朋友，是否客服腔
- empathy 共情：是否接住情绪
- persona 人设：是否像同一个她、前后一致
- length 长度：是否合适
整体 rating: good / mid / bad
silent: 这条是否本来就不该回（true/false）
comment: 一句话具体问题

只输出一行 JSON：
{"relevance":1,"natural":1,"empathy":1,"persona":1,"length":1,"rating":"good","silent":false,"comment":""}"""


def key(s): return hashlib.sha256(s.encode()).hexdigest()


def ask(api, user, reply):
    prompt = PROMPT.replace("{user}", user).replace("{reply}", reply)
    body = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": prompt}], "max_tokens": 512, "temperature": 0}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": "Bearer " + api, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    raw = str(data["choices"][0]["message"]["content"] or "")
    try:
        start = raw.find("{"); return json.loads(raw[start:raw.rfind("}") + 1])
    except Exception:
        return {"relevance": 3, "natural": 3, "empathy": 3, "persona": 3, "length": 3, "rating": "mid", "silent": False, "comment": "parse_error"}


def run():
    api = os.getenv("DEEPSEEK_API_KEY") or ""
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = [(r, key(str(r["user_text"]) + "\0" + str(r["reply"]))) for r in rows if key(str(r["user_text"]) + "\0" + str(r["reply"])) not in cache]
    def work(item):
        r, k = item
        try: out = ask(api, str(r["user_text"]), str(r["reply"]))
        except Exception as e: out = {"rating": "mid", "comment": "ERR" + str(e)[:40]}
        with LOCK:
            cache[k] = out
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=8) as ex: list(ex.map(work, todo))
    out = []
    for r in rows:
        d = cache.get(key(str(r["user_text"]) + "\0" + str(r["reply"])), {})
        out.append({**r, **d, "judge": "deepseek"})
    with OUT.open("w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    dims = ["relevance", "natural", "empathy", "persona", "length"]
    means = {d: round(statistics.mean(float(r.get(d, 3)) for r in out), 3) for d in dims}
    return {"n": len(out), "means": means, "ratings": {k: sum(1 for r in out if r.get("rating") == k) for k in ("good", "mid", "bad")}, "silent": sum(1 for r in out if r.get("silent")), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
