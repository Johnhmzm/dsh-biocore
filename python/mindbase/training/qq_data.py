from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HIST = Path(r"C:\Users\Administrator\.astrbot_launcher\instances\4200c1fe-c355-4eed-bed3-30878989b5ad\core\data\plugin_data\astrbot_plugin_group_chat_plus\chat_history\aiocqhttp\group")
EXTRA = [Path(r"D:\group")]
DICE_ACT = Path(__file__).resolve().parent.parent / "data" / "dice_activity.jsonl"
OUT_MSG = Path(__file__).resolve().parent.parent / "data" / "qq_human_messages.jsonl"
OUT_GATE = Path(__file__).resolve().parent.parent / "data" / "gate_dataset_human.jsonl"

AT_RE = re.compile(r"\[At:(\d+)\|([^\]]*)\]")
PREFIX_RE = re.compile(r"^\[[^\]]+\]:\s*")
SELF_ID = "2523857277"
BOT_IDS = {"2523857277", "1433066844", "2571879927"}
BOT_NAMES = ("诺登", "能天使", "小能", "阿能", "Exusiai", "exusiai")


def hid(x: str) -> str:
    return hashlib.sha256(str(x).encode()).hexdigest()[:12]


def load_activity() -> dict:
    total = defaultdict(int)
    user_day = defaultdict(int)
    if DICE_ACT.exists():
        for line in DICE_ACT.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                total[r["date"]] += int(r.get("frq", 0))
                user_day[(r["date"], r["user"])] += int(r.get("frq", 0))
            except Exception:
                continue
    return {"total": dict(total), "user_day": dict(user_day)}


def parse_messages() -> list:
    out = []
    seen = set()
    files = list(sorted(HIST.glob("*.json"))) + [x for root in EXTRA for x in sorted(root.glob("*.json"))]
    for p in files:
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in rows:
            sender = r.get("sender") or {}
            uid = str(sender.get("user_id") or "")
            self_id = str(r.get("self_id") or SELF_ID)
            nick = str(sender.get("nickname") or "")
            if not uid or uid in BOT_IDS or uid == self_id or "诺登" in nick:
                continue
            if r.get("type") not in ("GroupMessage", None):
                continue
            raw = str(r.get("message_str") or "")
            text = PREFIX_RE.sub("", raw)
            ats = AT_RE.findall(text)
            text = AT_RE.sub(lambda m: "@" + (m.group(2) or m.group(1)), text).strip()
            if not text:
                continue
            if any(name in text or any(name in (a[1] or "") for a in ats) for name in BOT_NAMES):
                continue
            ts = int(r.get("timestamp") or 0)
            if not ts:
                continue
            key = (str(r.get("group_id") or p.stem), str(r.get("message_id") or ts), str(uid))
            if key in seen:
                continue
            seen.add(key)
            date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            out.append({
                "group": hid(str(r.get("group_id") or p.stem)),
                "user": hid(uid),
                "ts": ts,
                "date": date,
                "text": text,
                "at": [hid(a[0]) for a in ats],
                "at_names": [a[1] for a in ats],
            })
    out.sort(key=lambda x: (x["group"], x["ts"]))
    return out


def build(msgs: list, window: int = 180) -> list:
    act = load_activity()
    by_group = defaultdict(list)
    for m in msgs:
        by_group[m["group"]].append(m)
    rows = []
    for g, arr in by_group.items():
        for i, m in enumerate(arr):
            nxt = arr[i + 1] if i + 1 < len(arr) else None
            gap = (nxt["ts"] - m["ts"]) if nxt else None
            speak = bool(nxt and gap is not None and gap <= window)
            rows.append({
                "group": g,
                "ts": m["ts"],
                "date": m["date"],
                "user": m["user"],
                "context": [x["text"] for x in arr[max(0, i - 5):i + 1]],
                "at": m["at"],
                "next": nxt["text"] if speak else "",
                "gap": gap,
                "label": "speak" if speak else "silence",
                "dice_activity_total": act["total"].get(m["date"], 0),
                "dice_user_activity": act["user_day"].get((m["date"], m["user"]), 0),
                "source": "qq_human",
                "label_source": "human_behavior",
            })
    return rows


def run() -> dict:
    msgs = parse_messages()
    rows = build(msgs)
    for path, data in ((OUT_MSG, msgs), (OUT_GATE, rows)):
        with path.open("w", encoding="utf-8") as fh:
            for r in data:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {
        "messages": len(msgs),
        "gate_rows": len(rows),
        "groups": len({m["group"] for m in msgs}),
        "users": len({m["user"] for m in msgs}),
        "labels": dict(Counter(r["label"] for r in rows)),
        "message_path": str(OUT_MSG),
        "gate_path": str(OUT_GATE),
    }


if __name__ == "__main__":
    print(run())
