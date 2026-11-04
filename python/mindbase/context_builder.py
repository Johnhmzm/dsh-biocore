"""Prioritized context blocks and a numeric reflection trigger.

MemGPT/Letta-style core blocks + Generative-Agents-style reflection threshold.
No LLM call in the decision; the LLM is only used when the trigger fires.
"""
from __future__ import annotations


class ContextBuilder:
    def __init__(self, max_chars: int = 1400):
        self.max_chars = int(max_chars)
        self.last_blocks = {}

    def build(self, facts=None, loops=None, recent: str = "") -> str:
        facts = [str(x).strip() for x in (facts or []) if str(x).strip()]
        loops = [str(x).strip() for x in (loops or []) if str(x).strip()]
        blocks = []
        used = 0
        def add(label, items):
            nonlocal used
            if not items:
                return
            body = "；".join(dict.fromkeys(items))
            text = label + "：" + body
            if used + len(text) > self.max_chars:
                remain = max(0, self.max_chars - used)
                if remain < 20:
                    return
                text = text[:remain]
            blocks.append(text)
            used += len(text)
        add("稳定事实", facts)
        add("未闭环", loops)
        if recent:
            add("近期", [recent])
        self.last_blocks = {"facts": len(facts), "loops": len(loops), "chars": used}
        return "\n".join(blocks)


class ReflectionTrigger:
    def __init__(self, threshold: float = 8.0, min_interval_s: float = 1800.0):
        self.threshold = float(threshold)
        self.min_interval_s = float(min_interval_s)
        self.accum = 0.0
        self.last_at = 0.0

    def note(self, importance: float) -> None:
        try:
            self.accum += max(0.0, min(1.0, float(importance)))
        except Exception:
            pass

    def due(self, now: float) -> bool:
        return self.accum >= self.threshold and (float(now) - self.last_at) >= self.min_interval_s

    def consume(self, now: float) -> None:
        self.accum = 0.0
        self.last_at = float(now)
