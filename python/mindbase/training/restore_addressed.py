from __future__ import annotations

import json
import re
from pathlib import Path

ROOTS = [
    Path(r"C:\Users\Administrator\.astrbot_launcher\instances\4200c1fe-c355-4eed-bed3-30878989b5ad\core\data\plugin_data\astrbot_plugin_group_chat_plus\chat_history\aiocqhttp\group"),
    Path(r"D:\group"),
]
OUT = Path(__file__).resolve().parent.parent / "data" / "addressed_to_agent.jsonl"
BOT_IDS = {"2523857277", "1433066844", "2571879927"}
NAMES = ("能天使", "小能", "阿能", "Exusiai", "exusiai")
AT_RE = re.compile(r"\[At:(\d+)\|([^\]]*)\]")
PREFIX_RE = re.compile(r"^\[[^\]]+\]:\s*")


def main() -> dict:
    by_group = {}
    for root in ROOTS:
        for p in sorted(root.glob("*.json")):
            try:
                rows = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for r in rows:
                g = str(r.get("group_id") or p.stem)
                by_group.setdefault(g, []).append(r)
    out = []
    for g, arr in by_group.items():
        arr.sort(key=lambda x: x.get("timestamp", 0))
        for i, r in enumerate(arr):
            sender = r.get("sender") or {}
            uid = str(sender.get("user_id") or "")
            if not uid or uid in BOT_IDS:
                continue
            raw = str(r.get("message_str") or "")
            text = PREFIX_RE.sub("", raw)
            ats = AT_RE.findall(text)
            text = AT_RE.sub(lambda m: "@" + (m.group(2) or m.group(1)), text).strip()
            hit = any(n in text or any(n in (a[1] or "") for a in ats) for n in NAMES)
            if not hit:
                continue
            ctx = []
            for x in arr[max(0, i - 6):i]:
                s = x.get("sender") or {}
                content = str(x.get("message_str") or "")
                content = PREFIX_RE.sub("", content)
                content = AT_RE.sub(lambda m: "@" + (m.group(2) or m.group(1)), content).strip()
                if content:
                    ctx.append(content[:220])
            nxt = arr[i + 1] if i + 1 < len(arr) else None
            out.append({
                "id": f"addr_{len(out):03d}",
                "group": hashlib_short(g),
                "ts": r.get("timestamp", 0),
                "context": ctx,
                "text": text[:400],
                "at": [a[1] or a[0] for a in ats if a[0] in BOT_IDS or any(n in (a[1] or "") for n in NAMES)],
                "is_question": int("?" in text or "？" in text),
                "actual_next": (PREFIX_RE.sub("", str(nxt.get("message_str") or ""))[:220] if nxt else ""),
            })
    with OUT.open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"n": len(out), "groups": len({r["group"] for r in out}), "path": str(OUT)}


def hashlib_short(x: str) -> str:
    import hashlib
    return hashlib.sha256(x.encode()).hexdigest()[:12]


if __name__ == "__main__":
    print(main())
