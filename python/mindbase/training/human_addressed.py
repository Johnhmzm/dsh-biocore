from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOTS = [Path(r"C:\Users\Administrator\.astrbot_launcher\instances\4200c1fe-c355-4eed-bed3-30878989b5ad\core\data\plugin_data\astrbot_plugin_group_chat_plus\chat_history\aiocqhttp\group"), Path(r"D:\group")]
OUT = Path(__file__).resolve().parent.parent / "data" / "human_addressed.jsonl"
BOTS = {"2523857277", "1433066844", "2571879927"}
AT = re.compile(r"\[At:(\d+)\|([^\]]*)\]")
PREFIX = re.compile(r"^\[[^\]]+\]:\s*")


def clean(s):
    s = PREFIX.sub("", str(s or ""))
    return AT.sub(lambda m: "@" + (m.group(2) or m.group(1)), s).strip()


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
            sender = m.get("sender") or {}
            uid = str(sender.get("user_id") or "")
            if not uid or uid in BOTS: continue
            ats = [a for a in AT.findall(str(m.get("message_str") or "")) if a[0] not in BOTS]
            if not ats: continue
            text = clean(m.get("message_str"))
            ts = m.get("timestamp", 0)
            recent = [x for x in arr[max(0, i - 20):i + 1] if ts - x.get("timestamp", 0) <= 600]
            for target, name in list({a[0]: a[1] for a in ats}.items()):
                def target_msgs():
                    return [x for x in arr[max(0, i - 20):i] if str((x.get("sender") or {}).get("user_id") or "") == target]
                tm = target_msgs()
                last = max((x.get("timestamp", 0) for x in tm), default=0)
                replied = any(str((x.get("sender") or {}).get("user_id") or "") == target and 0 <= x.get("timestamp", 0) - ts <= 180 for x in arr[i + 1:i + 20])
                out.append({
                    "group": __import__("hashlib").sha256(g.encode()).hexdigest()[:12],
                    "user": __import__("hashlib").sha256(uid.encode()).hexdigest()[:12],
                    "target": __import__("hashlib").sha256(target.encode()).hexdigest()[:12],
                    "target_name": name,
                    "ts": ts,
                    "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                    "context": [clean(x.get("message_str")) for x in arr[max(0, i - 5):i + 1]],
                    "text": text[:300],
                    "target_at": 1,
                    "target_recent": sum(1 for x in recent if str((x.get("sender") or {}).get("user_id") or "") == target),
                    "target_gap": (ts - last) if last else 9999,
                    "group_recent": len(recent),
                    "since_last": (ts - arr[i - 1].get("timestamp", ts)) if i else 9999,
                    "group_size": len({str((x.get("sender") or {}).get("user_id") or "") for x in arr}),
                    "hour": datetime.fromtimestamp(ts).hour / 24.0,
                    "message_len": min(len(text), 200) / 200.0,
                    "is_question": int("?" in text or "？" in text),
                    "has_at": 1,
                    "target_reciprocity": sum(1 for x in recent if str((x.get("sender") or {}).get("user_id") or "") == target) / max(1, len(recent)),
                    "label": "speak" if replied else "silence",
                })
    out.sort(key=lambda r: r["ts"])
    with OUT.open("w", encoding="utf-8") as fh:
        for r in out: fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    return {"n": len(out), "labels": dict(Counter(r["label"] for r in out)), "groups": len({r["group"] for r in out}), "targets": len({r["target"] for r in out}), "path": str(OUT)}


if __name__ == "__main__":
    print(main())
