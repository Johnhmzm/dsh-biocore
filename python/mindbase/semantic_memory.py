from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from .types import LLMRequest


def _json(text: str) -> Dict[str, object]:
    text = str(text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


class SemanticMemory:
    """LLM-based fact extraction / memory question / evidence judge. No keyword tables."""

    def __init__(self, client):
        self.client = client

    def _ask(self, system: str, user: str, max_tokens: int = 300) -> Dict[str, object]:
        req = LLMRequest(mode="semantic", urgency=0.5, max_tokens=max_tokens, reason="semantic",
                         context={"system": system, "user": user})
        raw = self.client.generate(req)
        self.last_raw = raw
        return _json(raw)

    def parse(self, text: str) -> Dict[str, object]:
        out = self._ask(
            "你只做信息抽取。只输出 JSON，不要解释。",
            "用户说：" + str(text) +
            "\n返回 JSON：{\"is_fact\": true/false, \"fact\": \"关于用户的事实，没有则空\", "
            "\"is_memory_question\": true/false, \"query\": \"如果是在问记忆，提取查询关键词/实体，否则空\"}",
            220,
        )
        return {
            "is_fact": bool(out.get("is_fact")),
            "fact": str(out.get("fact") or "").strip(),
            "is_memory_question": bool(out.get("is_memory_question")),
            "query": str(out.get("query") or "").strip(),
        }

    def reply(self, text: str, memories: List[str], persona: str = "", recent: Optional[List[str]] = None, insight: str = "", state: str = "", diary: str = "", preference: str = "", emotion: str = "", noise: float = 0.0) -> Dict[str, object]:
        system = (persona.strip() + "\n\n" if persona else "") + (
            "你在做一次完整的社交回复。只输出 JSON，不要 markdown，不要解释。"
            "如果对方在问记忆，只能用给出的记忆回答；记忆不支持就诚实说不知道，不要编。"
        )
        user = (
            "最近对话：\n" + "\n".join("- " + str(x) for x in (recent or [])[-4:]) +
            "\n记忆：\n" + "\n".join("- " + str(m) for m in memories[:6]) +
            ("\n你现在的身体/情绪状态：" + str(state) if state else "") +
            ("\n你最近的日记（可参考，不要照念）：" + str(diary)[:400] if diary else "") +
            ("\n这个用户的表达偏好：" + str(preference) if preference else "") +
            ("\n你当前的需要和情绪：" + str(emotion) if emotion else "") +
            ("\n轻微情绪噪声：" + str(round(float(noise), 3)) if noise else "") +
            ("\n你刚冒出的念头（可自然带出，不强制，不要复述）：" + str(insight) if insight else "") +
            "\n用户刚说：" + str(text) +
            "\n返回 JSON：{\"reply\": \"对用户说的一句中文，1-3句\", \"facts\": [\"关于用户的新事实，没有则空数组\"], \"memory_used\": \"用到的记忆，没有则空\", "
            "\"appraisal\": {\"valence\": -1到1, \"need_delta\": -1到1, \"fear_delta\": -1到1, \"joy_delta\": -1到1, "
            "\"agency\": 0到1, \"certainty\": 0到1, \"control\": 0到1, \"novelty\": 0到1}}"
        )
        raw = ""
        try:
            req = LLMRequest(mode="semantic", urgency=0.5, max_tokens=320, reason="semantic_reply",
                             context={"system": system, "user": user})
            raw = str(self.client.generate(req) or "")
            out = _json(raw)
        except Exception:
            out = {}
        reply = str(out.get("reply") or "").strip()
        if not reply:
            reply = raw.strip()
        facts = out.get("facts") or []
        if isinstance(facts, str):
            facts = [facts]
        if not isinstance(facts, list):
            facts = []
        appraisal = out.get("appraisal") or {}
        if not isinstance(appraisal, dict):
            appraisal = {}
        return {"reply": reply, "facts": [str(x) for x in facts if str(x).strip()], "memory_used": str(out.get("memory_used") or ""), "appraisal": appraisal}

    def sentiment(self, text: str) -> float:
        out = self._ask("判断用户这句话的情绪，只输出 JSON。", "用户说：" + str(text) + "\n返回 JSON：{\"sentiment\": -1 到 1 的数}", 300)
        try:
            return max(-1.0, min(1.0, float(out.get("sentiment", 0.0))))
        except Exception:
            return 0.0

    def judge(self, question: str, memories: List[str]) -> Optional[str]:
        out = self._ask(
            "你只根据给出的记忆回答。没有证据就诚实说不知道，不要编。只输出 JSON。",
            "问题：" + str(question) +
            "\n记忆：\n" + "\n".join("- " + str(m) for m in memories[:6]) +
            "\n返回 JSON：{\"supported\": true/false, \"answer\": \"用一句中文回答；supported=false 时写 不知道\"}",
            240,
        )
        if not out.get("supported"):
            return None
        answer = str(out.get("answer") or "").strip()
        return answer or None
