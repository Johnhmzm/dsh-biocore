from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .feedback_report import report as feedback_report

LOGS = [
    Path(__file__).resolve().parent.parent / "data" / "one_on_one_interactions.jsonl",
]
SIM = Path(__file__).resolve().parent.parent / "data" / "one_on_one_simulated.jsonl"
DIVERSE = Path(__file__).resolve().parent.parent / "data" / "one_on_one_diverse.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_checkpoint.json"


def load(path):
    if not path.exists(): return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarize(rows):
    if not rows: return {"n": 0}
    actions = {}
    for r in rows: actions[str(r.get("action"))] = actions.get(str(r.get("action")), 0) + 1
    lens = [len(str(r.get("reply") or "")) for r in rows if r.get("reply")]
    ids = [float(r.get("state_after", {}).get("self", {}).get("identity", 0)) for r in rows if r.get("state_after")]
    stresses = [float(r.get("state_after", {}).get("affect", {}).get("stress", 0)) for r in rows if r.get("state_after")]
    return {
        "n": len(rows),
        "actions": actions,
        "avg_reply_len": round(statistics.mean(lens), 1) if lens else 0,
        "identity_range": [round(min(ids), 3), round(max(ids), 3)] if ids else [],
        "avg_stress": round(statistics.mean(stresses), 3) if stresses else 0,
        "llm_requests": sum(1 for r in rows if r.get("llm_request")),
    }


def run() -> dict:
    all_rows = []
    per = {}
    for p in LOGS:
        rows = load(p)
        per[p.name] = summarize(rows)
        all_rows += rows
    simulated = summarize(load(SIM)) if SIM.exists() else {"n": 0}
    diverse = summarize(load(DIVERSE)) if DIVERSE.exists() else {"n": 0}
    # windows of 20 turns
    windows = []
    for i in range(0, len(all_rows), 20):
        windows.append(summarize(all_rows[i:i + 20]))
    ckpt = {"ts": time.time(), "total": summarize(all_rows), "sources": per, "simulated_reference": simulated, "diverse_reference": diverse, "feedback": feedback_report(), "windows": windows}
    OUT.write_text(json.dumps(ckpt, ensure_ascii=False, indent=2), encoding="utf-8")
    return ckpt


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
