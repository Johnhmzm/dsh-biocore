from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "data" / "qq_human_messages.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def build(window: int = 180) -> list:
    msgs = [json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]
    by = defaultdict(list)
    for m in msgs:
        by[m["group"]].append(m)
    rng = random.Random(0)
    rows = []
    for group, arr in by.items():
        arr.sort(key=lambda x: x["ts"])
        last_seen = {}
        for i, m in enumerate(arr):
            prev_ts = m["ts"] - arr[i - 1]["ts"] if i else None
            nxt = arr[i + 1] if i + 1 < len(arr) else None
            gap = (nxt["ts"] - m["ts"]) if nxt else None
            recent = [x for x in arr[max(0, i - 20):i + 1] if m["ts"] - x["ts"] <= 600]
            group_recent = len(recent)
            # positive: next speaker
            targets = []
            if nxt and gap is not None and gap <= window:
                targets.append((nxt["user"], 1))
            # negative: an active member who is not next speaker
            others = [x["user"] for x in recent if x["user"] != (nxt["user"] if nxt else None) and x["user"] != m["user"]]
            if others:
                targets.append((rng.choice(others), 0))
            for target, label in targets:
                target_prev = last_seen.get(target)
                target_recent = sum(1 for x in recent if x["user"] == target)
                last = m["text"]
                rows.append({
                    "group": group,
                    "ts": m["ts"],
                    "date": m["date"],
                    "context": [x["text"] for x in arr[max(0, i - 5):i + 1]],
                    "target": target,
                    "at": m["at"],
                    "target_at": int(target in m["at"]),
                    "target_recent": target_recent,
                    "target_gap": (m["ts"] - target_prev) if target_prev else 9999,
                    "group_recent": group_recent,
                    "since_last": prev_ts if prev_ts is not None else 9999,
                    "group_size": len({x["user"] for x in arr}),
                    "hour": __import__("datetime").datetime.fromtimestamp(m["ts"]).hour / 24.0,
                    "message_len": min(len(last), 200) / 200.0,
                    "is_question": int("?" in last or "？" in last),
                    "has_at": int(bool(m["at"])),
                    "target_reciprocity": target_recent / max(1, group_recent),
                    "label": "speak" if label else "silence",
                })
            last_seen[m["user"]] = m["ts"]
    rng.shuffle(rows)
    return rows


def run() -> dict:
    rows = build()
    with OUT.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    return {"rows": len(rows), "labels": dict(Counter(r["label"] for r in rows)), "groups": len({r["group"] for r in rows}), "path": str(OUT)}


if __name__ == "__main__":
    print(run())
