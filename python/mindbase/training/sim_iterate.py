from __future__ import annotations

import json
from pathlib import Path

from mindbase import MindBase, MindConfig
from mindbase.types import Event

DATA = Path(__file__).resolve().parent.parent / "data" / "one_on_one_diverse.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "sim_iteration.json"


def replay(rows, cfg):
    mb = MindBase(cfg)
    actions = {}
    ids, stresses = [], []
    for r in rows:
        out = mb.step(Event(text=str(r.get("event") or ""), speaker="A", directed=True, ts=mb.t + 60, domain="one_on_one"))
        mb.post_reply(str(r.get("reply") or "嗯"), feedback=0.2, speaker="A")
        actions[out.action] = actions.get(out.action, 0) + 1
        ids.append(round(mb.self_model.identity, 3))
        stresses.append(round(mb.affect.stress, 3))
    return {
        "n": len(rows),
        "actions": actions,
        "identity": [min(ids), max(ids)] if ids else [],
        "stress": round(sum(stresses) / max(1, len(stresses)), 4),
        "health": mb.health()["ok"],
    }


def main() -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(rows) < 20:
        return {"status": "insufficient", "n": len(rows)}
    base = replay(rows, MindConfig(seed=42))
    cand = replay(rows, MindConfig(seed=42, stress_tau=150.0))
    drift = abs((sum(base["identity"]) / 2) - (sum(cand["identity"]) / 2)) if base["identity"] and cand["identity"] else 0
    accepted = bool(cand["health"] and drift <= 0.15 and cand["stress"] <= base["stress"])
    result = {
        "simulated": True,
        "n": len(rows),
        "baseline": base,
        "candidate": cand,
        "candidate_change": {"stress_tau": 150.0},
        "identity_drift": round(drift, 3),
        "accepted": accepted,
        "decision": "accept" if accepted else "rollback",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
