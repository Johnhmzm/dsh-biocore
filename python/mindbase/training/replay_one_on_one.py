from __future__ import annotations

import json
from pathlib import Path

from mindbase import MindBase, MindConfig
from mindbase.types import Event

LOG = Path(__file__).resolve().parent.parent / "data" / "one_on_one_interactions.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "one_on_one_replay.json"


def replay(rows, seed: int = 42) -> dict:
    mb = MindBase(MindConfig(seed=seed))
    actions = {}
    identity = []
    stress = []
    for r in rows:
        out = mb.step(Event(text=str(r.get("event") or ""), speaker="A", directed=True, ts=mb.t + 60, domain="one_on_one"))
        feedback = 0.2 if r.get("user_response") else -0.5
        mb.post_reply(str(r.get("reply") or "嗯"), feedback=feedback, speaker="A")
        actions[out.action] = actions.get(out.action, 0) + 1
        identity.append(round(mb.self_model.identity, 3))
        stress.append(round(mb.affect.stress, 3))
    return {
        "n": len(rows),
        "actions": actions,
        "identity": [min(identity), max(identity)] if identity else [],
        "stress": [min(stress), max(stress)] if stress else [],
        "health": mb.health()["ok"],
    }


def evaluate(path: Path | str | None = None) -> dict:
    p = Path(path or LOG)
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
    if len(rows) < 10:
        return {"n": len(rows), "status": "insufficient", "note": "need >=10 real 1v1 turns"}
    result = replay(rows)
    old = {
        "actions": {},
        "identity": [],
    }
    for r in rows:
        old["actions"][str(r.get("action"))] = old["actions"].get(str(r.get("action")), 0) + 1
        v = r.get("state_after", {}).get("self", {}).get("identity")
        if v is not None: old["identity"].append(float(v))
    drift = abs(sum(result["identity"]) / 2 - sum(old["identity"]) / max(1, len(old["identity"]))) if result["identity"] and old["identity"] else 0
    accepted = result["health"] and drift < 0.15
    result.update({"baseline_actions": old["actions"], "identity_drift": round(drift, 3), "accepted": accepted})
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
