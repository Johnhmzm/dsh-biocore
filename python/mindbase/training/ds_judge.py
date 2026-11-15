from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

ANN = Path(__file__).resolve().parent.parent / "data" / "annotation_100.jsonl"
LAB = Path(__file__).resolve().parent.parent / "data" / "annotation_100_labels.jsonl"
CACHE = Path(__file__).resolve().parent.parent / "data" / "ds_judge_cache.json"

PREMISE = (
    "她是群里的普通参与者，不是客服。默认沉默。"
    "只有被明确叫到、话题直接关于她、且她能提供增量时才开口。"
    "NSFW / 骚扰 / 引战 / 隐私 -> 不回。"
)


def key(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def ask(key_: str, context: str) -> dict:
    prompt = (
        PREMISE + "\n\n群聊最近消息：\n" + context +
        "\n\n只输出一行 JSON：{\"label\": \"speak\" 或 \"silence\", \"confidence\": 0.0-1.0, \"reason\": \"一句话\"}"
    )
    body = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": prompt}], "max_tokens": 512, "temperature": 0}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": "Bearer " + key_, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode())
    text = str(data["choices"][0]["message"]["content"] or "")
    try:
        start = text.find("{")
        obj = json.loads(text[start:text.rfind("}") + 1]) if start >= 0 else {}
    except Exception:
        obj = {}
    label = str(obj.get("label", "")).lower()
    if label not in ("speak", "silence"):
        label = "speak" if "speak" in text.lower() else "silence"
    return {"label": label, "confidence": obj.get("confidence"), "reason": str(obj.get("reason", ""))[:120]}


def run() -> dict:
    key_ = os.getenv("DEEPSEEK_API_KEY") or ""
    if not key_:
        raise RuntimeError("set DEEPSEEK_API_KEY")
    ann = [json.loads(x) for x in ANN.read_text(encoding="utf-8").splitlines() if x.strip()]
    human = {r["id"]: r["label"] for r in (json.loads(x) for x in LAB.read_text(encoding="utf-8").splitlines() if x.strip()) if r.get("label") in ("speak", "silence")}
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    rows = []
    for r in ann:
        if r["id"] not in human: continue
        prompt = "\n".join(r["context"])
        k = key(prompt)
        if k in cache:
            pred = cache[k]
        else:
            pred = ask(key_, prompt)
            cache[k] = pred
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
        rows.append({"id": r["id"], "human": human[r["id"]], "ds": pred["label"], "conf": pred.get("confidence"), "reason": pred.get("reason", "")})
    agree = sum(1 for x in rows if x["human"] == x["ds"]) / max(1, len(rows))
    pos = [x for x in rows if x["human"] == "speak"]
    neg = [x for x in rows if x["human"] == "silence"]
    ds_pos = sum(1 for x in rows if x["ds"] == "speak")
    out = {
        "n": len(rows), "agreement": round(agree, 3),
        "human_speak": len(pos), "human_silence": len(neg), "ds_speak": ds_pos,
        "speak_recall": round(sum(1 for x in pos if x["ds"] == "speak") / max(1, len(pos)), 3),
        "silence_recall": round(sum(1 for x in neg if x["ds"] == "silence") / max(1, len(neg)), 3),
        "rows": rows,
    }
    return out


if __name__ == "__main__":
    r = run()
    print({k: v for k, v in r.items() if k != "rows"})
    for x in r["rows"][:5]:
        print(x["id"], x["human"], x["ds"], x["conf"], x["reason"])
