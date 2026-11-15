from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOTS = [Path(r"C:\Users\Administrator\.astrbot_launcher\instances\4200c1fe-c355-4eed-bed3-30878989b5ad\core\data\plugin_data\astrbot_plugin_group_chat_plus\chat_history\aiocqhttp\group"), Path(r"D:\group")]
OUT = Path(__file__).resolve().parent.parent / "data" / "human_thread.jsonl"
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
            for r in rows:
                by[str(r.get("group_id") or p.stem)].append(r)
    events = []
    for g, arr in by.items():
        arr.sort(key=lambda x: x.get("timestamp", 0))
        for i, m in enumerate(arr):
            uid = str((m.get("sender") or {}).get("user_id") or "")
            if not uid or uid in BOTS: continue
            ats = [a for a in AT.findall(str(m.get("message_str") or "")) if a[0] not in BOTS]
            if not ats: continue
            ts = m.get("timestamp", 0)
            for target, name in list({a[0]: a[1] for a in ats}.items()):
                after = arr[i + 1:i + 80]
                def spoke(window_s, max_turns):
                    selected = []
                    for x in after:
                        if x.get("timestamp", 0) - ts > window_s: break
                        selected.append(x)
                        if len(selected) >= max_turns: break
                    return any(str((x.get("sender") or {}).get("user_id") or "") == target for x in selected)
                events.append({
                    "id": f"thr_{len(events):04d}",
                    "group": hashlib.sha256(g.encode()).hexdigest()[:12],
                    "target": hashlib.sha256(target.encode()).hexdigest()[:12],
                    "target_name": name,
                    "ts": ts,
                    "text": clean(m.get("message_str"))[:300],
                    "speak_180s": int(spoke(180, 20)),
                    "speak_600s": int(spoke(600, 20)),
                    "speak_1800s": int(spoke(1800, 40)),
                    "speak_10turn": int(spoke(1800, 10)),
                    "speak_5turn": int(spoke(600, 5)),
                })
    with OUT.open("w", encoding="utf-8") as fh:
        for e in events: fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    stats = {k: {"n": len(events), "speak": sum(e[k] for e in events), "rate": round(sum(e[k] for e in events) / max(1, len(events)), 3)} for k in ("speak_180s", "speak_600s", "speak_1800s", "speak_5turn", "speak_10turn")}
    return {"events": len(events), "targets": len({e["target"] for e in events}), "stats": stats, "path": str(OUT)}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
