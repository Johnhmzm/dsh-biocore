"""Numeric social-feedback judge.

LLM only turns semantics into numbers; decisions/learning stay numeric.
"""
from __future__ import annotations

import json
from typing import Dict, Optional


def _f(x, default=0.0):
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return float(default)


class SocialFeedbackJudge:
    def __init__(self, client):
        self.client = client

    def judge(self, user_text: str, last_reply: str, recent: str = "",
              user_affect: Optional[Dict[str, float]] = None) -> Optional[Dict[str, object]]:
        if self.client is None:
            return None
        p = (
            "判断用户这句话是不是在指出‘她上一句/上一行为做错了’。只输出 JSON："
            "{\"violation\":0或1,"
            "\"legitimacy\":0..1,"
            "\"severity\":0..1,"
            "\"blame\":0..1,"
            "\"intent\":0..1,"
            "\"internal\":0..1,"
            "\"repair_expectation\":0..1,"
            "\"relationship_certainty\":0..1,"
            "\"coping_potential\":0..1,"
            "\"norm_compatibility\":0..1,"
            "\"behavior_key\":\"简短行为标识\"}。"
            "如果用户只是在骂外部事物/别人，不是在评价她，violation=0。"
            "如果只是玩笑/撒娇，severity 要低。"
            "不要输出解释。\n\n"
            "[用户] " + str(user_text or "")[:600] +
            "\n[她上一句] " + str(last_reply or "")[:600] +
            "\n[最近] " + str(recent or "")[:800]
        )
        try:
            req = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=0, reason="social_feedback_judge",
                response_format={"type": "json_object"},
                context={"system": "你是社会反馈评估器，只输出数字 JSON。", "user": p})
            raw = str(self.client.generate(req))
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            out = {
                "violation": 1.0 if float(data.get("violation") or 0) >= 0.5 else 0.0,
                "legitimacy": _f(data.get("legitimacy"), 0.5),
                "severity": _f(data.get("severity"), 0.5),
                "blame": _f(data.get("blame"), 1.0),
                "intent": _f(data.get("intent"), 0.0),
                "internal": _f(data.get("internal"), 0.5),
                "repair_expectation": _f(data.get("repair_expectation"), 0.5),
                "relationship_certainty": _f(data.get("relationship_certainty"), 1.0),
                "coping_potential": _f(data.get("coping_potential"), 0.5),
                "norm_compatibility": _f(data.get("norm_compatibility"), 0.5),
                "behavior_key": str(data.get("behavior_key") or "behavior")[:80],
            }
            return out
        except Exception:
            return None


class RepairJudge:
    def __init__(self, client):
        self.client = client

    def judge(self, user_text: str, last_reply: str = "", recent: str = "") -> dict:
        if self.client is None:
            return {"repair": 0.0, "effort": 0.0, "accepted": 0.0}
        p = ("判断用户是不是在原谅/接受她的道歉/主动修复关系。只输出 JSON："
             "{\"repair\":0或1,\"effort\":0..1,\"accepted\":0或1}。"
             "如果只是普通客气，repair=0。不要解释。\n"
             "[用户] " + str(user_text or "")[:500] +
             "\n[她上一句] " + str(last_reply or "")[:400] +
             "\n[最近] " + str(recent or "")[:500])
        try:
            req = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=0, reason="repair_judge",
                response_format={"type": "json_object"},
                context={"system": "你是关系修复评估器，只输出数字 JSON。", "user": p})
            raw = str(self.client.generate(req))
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            return {"repair": 1.0 if float(data.get("repair") or 0) >= 0.5 else 0.0,
                    "effort": _f(data.get("effort"), 0.5),
                    "accepted": 1.0 if float(data.get("accepted") or 0) >= 0.5 else 0.0}
        except Exception:
            return {"repair": 0.0, "effort": 0.0, "accepted": 0.0}
