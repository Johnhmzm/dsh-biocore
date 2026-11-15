from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

ROOTS = [Path(r"C:\Users\Administrator\.astrbot_launcher\instances\4200c1fe-c355-4eed-bed3-30878989b5ad\core\data\plugin_data\astrbot_plugin_group_chat_plus\chat_history\aiocqhttp\group"), Path(r"D:\group")]
OUT = Path(__file__).resolve().parent.parent / "data" / "exusiai_1v1_records.jsonl"
AGENT = "2523857277"
BOTS = {"2523857277", "1433066844", "2571879927"}
AT = re.compile(r"\[At:(\d+)\|([^\]]*)\]")
PREFIX = re.compile(r"^\[[^\]]+\]:\s*")


def clean(s):
    return AT.sub(lambda m: "@" + (m.group(2) or m.group(1)), PREFIX.sub("", str(s or ""))).strip()


def main() -> dict:
    by = defaultdict(list)
    for root in ROOTS:
        for p in sorted(root.glob("*.json")):
            try: rows = json.loads(p.read_text(encoding="utf-8"))
            except Exception: continue
            for r in rows: by[str(r.get("group_id") or p.stem)].append(r)
    out = []
    for g, arr in by.items():
        arr.sort(key=lambda x: x.get("timestamp", 0))
        for i, bot in enumerate(arr):
            if str((bot.get("sender") or {}).get("user_id") or "") != AGENT:
                continue
            ts = bot.get("timestamp", 0)
            prev = [x for x in arr[max(0, i - 6):i] if ts - x.get("timestamp", 0) <= 900 and str((x.get("sender") or {}).get("user_id") or "") not in BOTS]
            if not prev: continue
            user = prev[-1]
            out.append({
                "id": f"exu_{len(out):04d}",
                "group": hashlib.sha256(g.encode()).hexdigest()[:12],
                "ts": ts,
                "user": hashlib.sha256(str((user.get("sender") or {}).get("user_id") or "").encode()).hexdigest()[:12],
                "context": [clean(x.get("message_str"))[:200] for x in prev[:-1]],
                "user_text": clean(user.get("message_str"))[:300],
                "reply": clean(bot.get("message_str"))[:500],
                "latency": ts - user.get("timestamp", ts),
            })
    with OUT.open("w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"n": len(out), "groups": len({r["group"] for r in out}), "median_latency": sorted(r["latency"] for r in out)[len(out)//2] if out else None, "path": str(OUT)}


if __name__ == "__main__":
    print(main())
