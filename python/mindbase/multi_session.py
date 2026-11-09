from __future__ import annotations

from mindbase import MindBase, MindConfig
from mindbase.types import Event


def run() -> dict:
    mb = MindBase(MindConfig(seed=555))
    mb.step(Event(text="我叫阿明，我喜欢猫", speaker="A", directed=True, ts=0))
    mb.post_reply("记住了，阿明喜欢猫。", feedback=0.8, speaker="A")
    mb.tick(86400)
    out = mb.step(Event(text="你记得我叫什么吗？", speaker="A", directed=True, ts=mb.t + 60))
    top = mb.memory.retrieve_bm25("阿明 猫", mb.t, k=3)
    texts = [x.text for x in top]
    return {"reply_action": out.action, "memory": texts, "recalled_name": any("阿明" in t for t in texts), "recalled_cat": any("猫" in t for t in texts)}


if __name__ == "__main__":
    print(run())
