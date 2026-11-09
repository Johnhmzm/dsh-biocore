from __future__ import annotations

from typing import Dict

from .grid_world import EmbodiedLoop, GridWorld
from .relationship_session import RelationshipSession


def run(stress: bool = False, extreme: bool = False) -> Dict[str, object]:
    from . import MindBase, MindConfig

    # 1. autonomy: can it act without being addressed and stay viable?
    mb1 = MindBase(MindConfig(seed=2000))
    noise = 0.60 if extreme else (0.40 if stress else 0.10)
    shock_every = 50 if stress else 100
    a = EmbodiedLoop(mb1, GridWorld(seed=1, noise=noise)).run(300, shock_every)
    autonomy = {
        "initiative": a["autonomy"]["initiative"],
        "survival": a["energy"],
        "viability": a["viability"],
        "pass": a["autonomy"]["initiative"] > 0.5 and a["energy"] > 0.2 and a["viability"] >= 0.6,
    }

    # 2. long-term memory: cross-day recall
    mb2 = MindBase(MindConfig(seed=2001))
    s = RelationshipSession(mb2)
    s.say("我叫阿明，我喜欢猫", 0.5)
    s.say("今天有点难过", 0.3)
    s.day_break(24 * 7 if stress else 24)
    recall = s.recall("阿明")
    memory = {"recall": recall, "pass": bool(recall and "阿明" in recall)}

    # 2b. memory interference: newer similar fact must supersede the old one
    mb2b = MindBase(MindConfig(seed=2002))
    sb = RelationshipSession(mb2b)
    sb.say("我的名字是阿明", 0.8)
    sb.say("我的名字是小华", 0.8)
    recalled = sb.recall("名字")
    interference = {"recall": recalled, "pass": bool(recalled and "小华" in recalled and "阿明" not in recalled)}

    # 3. identity persistence: conflict then repair
    before = mb2.self_model.identity
    for _ in range(15 if extreme else (5 if stress else 1)):
        s.say("你根本不懂我", -1.0)
    low = mb2.self_model.identity
    s.auto_repair(max_rounds=5 if extreme else 20)
    after = mb2.self_model.identity
    identity = {
        "before": round(before, 3), "after_conflict": round(low, 3), "after_repair": round(after, 3),
        "pass": after > 0.45 and abs(after - before) < 0.25,
    }

    # 4. relationship continuity: trust does not collapse after conflict+repair
    rel = {"trust": round(mb2.social.trust_for("A"), 3), "stage": mb2.social.stage("A")}
    rel["pass"] = rel["trust"] > 0.3

    checks = [autonomy["pass"], memory["pass"], interference["pass"], identity["pass"], rel["pass"]]
    return {
        "autonomy": autonomy, "memory": memory, "interference": interference, "identity": identity, "relationship": rel,
        "score": round(sum(checks) / len(checks), 3), "pass": all(checks),
    }
