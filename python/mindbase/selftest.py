from __future__ import annotations

from .config import MindConfig
from .engine import MindBase
from .types import Event


def main() -> None:
    cfg = MindConfig(seed=7, autonomy_threshold=0.05, abstain_threshold=0.30)
    mb = MindBase(cfg)

    events = [
        Event(text="你好", speaker="A", ts=0),
        Event(text="你今天真棒", speaker="A", ts=60),
        Event(text="你真废物", speaker="B", ts=120),
        Event(text="我今天好累", speaker="A", ts=180),
        Event(text="晚安", speaker="B", ts=240),
        Event(kind="tick", ts=300),
        Event(text="在吗", speaker="A", directed=True, ts=360),
    ]
    outs = mb.run(events)

    mb.assert_invariants()
    snap = mb.snapshot()
    assert "affect" in snap and "drives" in snap and "memory" in snap
    v = snap["affect"]["v"]
    a = snap["affect"]["a"]
    stress = snap["affect"]["stress"]
    assert -1.0 <= v <= 1.0, v
    assert 0.0 <= a <= 1.0, a
    assert 0.0 <= stress <= 1.0, stress

    assert any("废物" in item.text for item in mb.memory.items), "memory did not write"
    assert len(mb.wm.slots) <= cfg.wm_capacity
    assert outs[-1].action == "speak" and outs[-1].reason == "directed"
    assert outs[-1].llm_request is not None and outs[-1].llm_request.mode == "reply"

    # force an endogenous action with no external trigger
    mb.last_speaker = None
    mb.autonomy.h = 10.0
    mb.autonomy.last_spoke = -1e9
    mb.will.willpower = 1.0
    proactive = mb.tick(60)
    assert proactive.action == "speak" and proactive.reason in ("impulse", "intention"), proactive
    assert proactive.llm_request is not None and proactive.llm_request.mode == "proactive", proactive.llm_request

    # intention can veto impulse when willpower is high
    mb.intentions.add("stay quiet", action="silence", strength=0.9, commitment=0.9, now=mb.t)
    mb.will.willpower = 1.0
    mb.autonomy.h = 10.0
    mb.autonomy.last_spoke = -1e9
    veto = mb.tick(60)
    assert veto.action == "silence" and veto.reason == "intention", veto
    assert veto.llm_request is None, veto.llm_request

    # depleted willpower gives in to impulse
    mb.will.willpower = 0.0
    mb.autonomy.h = 10.0
    mb.autonomy.last_spoke = -1e9
    give_in = mb.tick(60)
    assert give_in.action == "speak" and give_in.reason == "impulse", give_in

    # mushroom body: reward increases prediction; no feedback does not
    p0, _ = mb.mb.predict("奖励线索")
    mb.post_reply("奖励线索", feedback=1.0)
    p1, _ = mb.mb.predict("奖励线索")
    assert p1 > p0, (p0, p1)

    p2, _ = mb.mb.predict("中性线索")
    mb.post_reply("中性线索", feedback=None)
    p3, _ = mb.mb.predict("中性线索")
    assert abs(p3 - p2) < 1e-9, (p2, p3)

    # two channels: positive feedback -> reward only
    b = mb.mb.predict_channels("正性线索")
    mb.post_reply("正性线索", feedback=1.0)
    a = mb.mb.predict_channels("正性线索")
    assert a["reward"] > b["reward"], (b, a)
    assert abs(a["punishment"] - b["punishment"]) < 1e-9, (b, a)

    # negative feedback -> punishment only, value drops
    b = mb.mb.predict_channels("负性线索")
    mb.post_reply("负性线索", feedback=-1.0)
    a = mb.mb.predict_channels("负性线索")
    assert a["punishment"] > b["punishment"], (b, a)
    assert a["value"] < b["value"], (b, a)

    # APL feedback loop: repeated high density -> lower gain
    g0 = mb.mb.apl.step(list(range(64)), mb.mb.kc.n, 0.05)
    for _ in range(5):
        g1 = mb.mb.apl.step(list(range(64)), mb.mb.kc.n, 0.05)
    assert g1 < g0, (g0, g1)

    # homeostatic scaling: total absolute weight bounded
    mb.mb.mbon_reward.w = [1.0] * mb.mb.kc.n
    mb.mb.homeostatic(10.0)
    assert sum(abs(w) for w in mb.mb.mbon_reward.w) <= 10.001

    # habituation: repeated cue -> weaker; negative -> sensitize
    h0 = mb.mb.hab.response("重复线索")
    mb.learner.on_event("重复线索", 0.0)
    h1 = mb.mb.hab.response("重复线索")
    assert h1 < h0, (h0, h1)
    mb.learner.on_event("重复线索", -1.0)
    h2 = mb.mb.hab.response("重复线索")
    assert h2 > h1, (h1, h2)

    silent = mb.step(Event(kind="self_action", speaker="self", ts=mb.t + 60))
    assert silent.action == "silence" and silent.llm_request is None, silent

    mb.assert_invariants()
    print("mindbase selftest: OK")
    print("events:", len(outs), "| memory items:", len(mb.memory.items), "| wm:", len(mb.wm.slots))
    print("last state:", mb.prompt_state(proactive.action, proactive.reason))


if __name__ == "__main__":
    main()
