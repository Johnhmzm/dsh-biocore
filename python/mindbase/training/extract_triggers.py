from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOTS = [Path(r"C:\Users\Administrator\.astrbot_launcher\instances\4200c1fe-c355-4eed-bed3-30878989b5ad\core\data\plugin_data\astrbot_plugin_group_chat_plus\chat_history\aiocqhttp\group"), Path(r"D:\group")]
OUT = Path(__file__).resolve().parent.parent / "data" / "group_triggers_1v1.jsonl"
AGENT_ID = "2523857277"
BOTS = {"2523857277", "1433066844", "2571879927"}
NAMES = ("能天使", "小能", "阿能", "Exusiai", "exusiai")
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
            for r in rows:
                by[str(r.get("group_id") or p.stem)].append(r)
    out = []
    for g, arr in by.items():
        arr.sort(key=lambda x: x.get("timestamp", 0))
        for i, m in enumerate(arr):
            uid = str((m.get("sender") or {}).get("user_id") or "")
            if not uid or uid in BOTS: continue
            raw = str(m.get("message_str") or "")
            ats = AT.findall(raw)
            text = clean(raw)
            by_at = any(a[0] == AGENT_ID for a in ats)
            by_name = any(n in text or any(n in (a[1] or "") for a in ats) for n in NAMES)
            if not (by_at or by_name): continue
            ts = m.get("timestamp", 0)
            reply = None; rts = None
            for x in arr[i + 1:i + 20]:
                xid = str((x.get("sender") or {}).get("user_id") or "")
                if xid == AGENT_ID:
                    reply = clean(x.get("message_str"))[:500]
                    rts = x.get("timestamp", 0)
                    break
                if x.get("timestamp", 0) - ts > 180:
                    break
            out.append({
                "id": f"trg_{len(out):04d}",
                "group": hashlib.sha256(g.encode()).hexdigest()[:12],
                "ts": ts,
                "user": hashlib.sha256(uid.encode()).hexdigest()[:12],
                "context": [clean(x.get("message_str"))[:200] for x in arr[max(0, i - 5):i]],
                "user_text": text[:400],
                "at": [a[1] or a[0] for a in ats if a[0] == AGENT_ID or any(n in (a[1] or "") for n in NAMES)],
                "by_at": int(by_at),
                "by_name": int(by_name),
                "bot_reply": reply or "",
                "bot_latency": (rts - ts) if rts else None,
                "replied": int(bool(reply)),
            })
    with OUT.open("w", encoding="utf-8") as fh:
        for r in out: fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"triggers": len(out), "replied": sum(r["replied"] for r in out), "groups": len({r["group"] for r in out}), "by_at": sum(r["by_at"] for r in out), "by_name": sum(r["by_name"] for r in out), "path": str(OUT)}


if __name__ == "__main__":
    print(main())
