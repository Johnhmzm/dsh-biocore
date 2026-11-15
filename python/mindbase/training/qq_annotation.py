from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import torch

from .qq_rank import feat, pairs
from .qq_rank_gate import fit
from .qq_shadow import aggregate

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"
MSGS = Path(__file__).resolve().parent.parent / "data" / "qq_human_messages.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "annotation_100.jsonl"


def event_key(r):
    return (r["group"], r["ts"], tuple(r["context"]))


def main() -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    msgs = [json.loads(x) for x in MSGS.read_text(encoding="utf-8").splitlines() if x.strip()]
    next_by = defaultdict(list)
    for i, m in enumerate(msgs):
        next_by[m["group"]].append(m)
    next_text = {}
    for g, arr in next_by.items():
        arr.sort(key=lambda x: x["ts"])
        for i, m in enumerate(arr):
            next_text[(g, m["ts"])] = arr[i + 1]["text"] if i + 1 < len(arr) else ""
    by = defaultdict(list)
    for r in rows:
        by[event_key(r)].append(r)
    events = list(by.items())
    model, mu, sd = fit(pairs(rows))
    scored = []
    for (g, ts, ctx), ev in events:
        X = (torch.tensor([feat(r) for r in ev], dtype=torch.float32) - mu) / sd
        with torch.no_grad():
            margin = aggregate(model(X).detach().cpu().numpy().reshape(-1).tolist())["margin"]
        NSFW = ("黑丝", "白丝", "内衣", "裸", "胸", "性", "约炮", "娃娃", "裙", "色情", "做爱", "床")
        if any("[转发消息]" in c or "--- 转发内容 ---" in c for c in ctx):
            continue
        if any(term in c for c in ctx for term in NSFW):
            continue
        if sum(len(c) for c in ctx) > 1200 or max((len(c) for c in ctx), default=0) > 500:
            continue
        scored.append({"group": g, "ts": ts, "context": list(ctx), "at": ev[0].get("at", []), "margin": margin, "actual_next": next_text.get((g, ts), "")})
    scored.sort(key=lambda x: x["margin"])
    n = len(scored)
    # boundary: spread across margin distribution, plus @ events
    picks = [scored[int(i * (n - 1) / 99)] for i in range(100)]
    seen = set()
    out = []
    for p in picks:
        k = (p["group"], p["ts"])
        if k in seen: continue
        seen.add(k)
        out.append({
            "id": f"ann_{len(out):03d}",
            "group": p["group"],
            "ts": p["ts"],
            "context": p["context"],
            "at": p["at"],
            "ranker_margin": round(float(p["margin"]), 4),
            "actual_next": p["actual_next"],
            "question": "如果她在这个群，此时该不该开口？",
            "label": None,
            "reason": "",
        })
    if len(out) < 100:
        for p in scored:
            k = (p["group"], p["ts"])
            if k in seen: continue
            seen.add(k)
            out.append({
                "id": f"ann_{len(out):03d}",
                "group": p["group"],
                "ts": p["ts"],
                "context": p["context"],
                "at": p["at"],
                "ranker_margin": round(float(p["margin"]), 4),
                "actual_next": p["actual_next"],
                "question": "如果她在这个群，此时该不该开口？",
                "label": None,
                "reason": "",
            })
            if len(out) >= 100: break
    with OUT.open("w", encoding="utf-8") as fh:
        for r in out: fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"annotations": len(out), "path": str(OUT), "groups": len({r["group"] for r in out})}


if __name__ == "__main__":
    print(main())
