from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

DICE = Path(r"C:\Users\Administrator\Desktop\Desktop\DiceDriver.LLOneBot\Dice1433066844")
OUT_ACT = Path(__file__).resolve().parent.parent / "data" / "dice_activity.jsonl"
OUT_EVT = Path(__file__).resolve().parent.parent / "data" / "dice_events.jsonl"


def hid(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def daily_rows() -> Iterable[dict]:
    for p in sorted((DICE / "user" / "daily").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = d.get("date") or []
        date_s = "-".join(f"{int(x):02d}" for x in date) if len(date) == 3 else p.stem.replace("daily_", "")
        for uid, rec in (d.get("user") or {}).items():
            if str(uid) == "0":
                continue
            yield {
                "source": "dice",
                "kind": "activity",
                "date": date_s,
                "user": hid(uid),
                "frq": int(rec.get("frq", 0)),
                "jrrp": rec.get("jrrp"),
            }


def event_rows() -> Iterable[dict]:
    for p in sorted((DICE / "audit").glob("*.txt")):
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for line in lines:
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            ts, level, text = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if not ts or len(ts) < 10:
                continue
            groups = re.findall(r"\[(.*?)\]\((\d+)\)", text)
            users = re.findall(r"([^\s(]+)\((\d{5,})\)", text)
            yield {
                "source": "dice",
                "kind": "event",
                "ts": ts,
                "level": level,
                "event": text[:80],
                "groups": [hid(g[1]) for g in groups],
                "users": [hid(u[1]) for u in users],
            }


def run() -> dict:
    n_act = n_evt = 0
    with OUT_ACT.open("w", encoding="utf-8") as fh:
        for row in daily_rows():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n"); n_act += 1
    with OUT_EVT.open("w", encoding="utf-8") as fh:
        for row in event_rows():
            fh.write(json.dumps(row, ensure_ascii=False) + "\n"); n_evt += 1
    return {"activity": n_act, "events": n_evt, "activity_path": str(OUT_ACT), "events_path": str(OUT_EVT)}


if __name__ == "__main__":
    print(run())
