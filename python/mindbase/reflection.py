"""Reflection engine: turn recent episodes into a self-note.

This is the "background reflection / daily summary" layer seen in
Shikigami-style companions; it writes back into the same memory framework
and can carry an emotional arc / open loops.
"""
from __future__ import annotations

import json
from typing import Dict, Optional


class ReflectionEngine:
    def __init__(self, client):
        self.client = client

    def run(self, longterm, mind=None, max_episodes: int = 16) -> Optional[object]:
        if self.client is None:
            return None
        rows = list(getattr(longterm, "episodes", [])[-int(max_episodes):])
        if len(rows) < 3:
            return None
        lines = []
        for e in rows:
            lines.append("用户：" + str(getattr(e, "user", ""))[:160])
            lines.append("她：" + str(getattr(e, "reply", ""))[:160])
            lines.append("情绪：" + str(getattr(e, "emotion", "")))
        loops = []
        try:
            loops = [x.summary for x in longterm.open_loops(6)]
        except Exception:
            pass
        v = 0.0
        stress = 0.0
        try:
            v = float(getattr(mind.affect, "v", 0.0))
            stress = float(getattr(mind.affect, "stress", 0.0))
        except Exception:
            pass
        prompt = (
            "把最近这段相处压缩成一条反思记忆。只输出 JSON："
            "{\"summary\":\"发生了什么\","
            "\"emotional_arc\":\"情绪怎么走的\","
            "\"self_insight\":\"她对自己/这段关系的理解\","
            "\"open_loops\":[\"仍没闭环的事\"]}。"
            "不要逐句复述；不要编造用户没说过的事实。\n\n"
            "当前 affect valence=%.2f stress=%.2f\n" % (v, stress) +
            "已有未闭环：" + (" / ".join(loops) or "（无）") + "\n\n" +
            "\n".join(lines)
        )
        try:
            req = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=360, reason="reflection",
                response_format={"type": "json_object"},
                context={"system": "你是记忆反思器，只输出 JSON。", "user": prompt})
            raw = str(self.client.generate(req))
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            summary = str(data.get("summary") or "").strip()
            if not summary:
                return None
            note = longterm.add_reflection(
                summary=summary, self_insight=str(data.get("self_insight") or ""),
                emotional_arc=str(data.get("emotional_arc") or ""),
                open_loops=[str(x) for x in (data.get("open_loops") or [])],
                valence=v)
            return note
        except Exception:
            return None
