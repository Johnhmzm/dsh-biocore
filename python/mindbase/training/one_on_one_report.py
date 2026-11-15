from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "data" / "one_on_one_interactions.jsonl"


def report(path: Path | str | None = None) -> dict:
    p = Path(path or LOG)
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
    if not rows:
        return {"n": 0, "note": "no 1v1 interactions yet"}
    actions = Counter(str(r.get("action")) for r in rows)
    acts = Counter(str(r.get("act")) for r in rows)
    responded = sum(1 for r in rows if r.get("user_response"))
    avg_latency = sum(float(r.get("latency") or 0) for r in rows) / len(rows)
    return {
        "n": len(rows),
        "sessions": len({r.get("session") for r in rows}),
        "actions": dict(actions),
        "acts": dict(acts),
        "user_response_rate": round(responded / len(rows), 3),
        "avg_latency": round(avg_latency, 2),
        "llm_requests": sum(1 for r in rows if r.get("llm_request")),
    }


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
