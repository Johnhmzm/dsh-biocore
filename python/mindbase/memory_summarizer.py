"""LLM/DSH memory consolidation: summarize episodes + extract durable facts."""
from __future__ import annotations

import json
from typing import Dict, List, Optional


class MemorySummarizer:
    def __init__(self, client, max_episodes: int = 12, fact_client=None):
        self.client = client
        self.fact_client = fact_client or client
        self.max_episodes = int(max_episodes)

    def __call__(self, episodes) -> Optional[Dict[str, object]]:
        if self.client is None:
            return None
        rows = list(episodes)[-self.max_episodes:]
        if not rows:
            return None
        lines = []
        for e in rows:
            lines.append(f"用户：{str(getattr(e, 'user', ''))[:180]}")
            lines.append(f"她：{str(getattr(e, 'reply', ''))[:180]}")
            lines.append(f"情绪：{getattr(e, 'emotion', '')}")
        prompt = (
            "把下面这段对话压缩成长期记忆。只输出 JSON："
            "{\"summary\":\"...\","
            "\"facts\":[{\"key\":\"名字/宠物/工作/偏好/事件\",\"text\":\"...\"}],"
            "\"open_loops\":[\"...\"],\"closed_loops\":[\"...\"]}。"
            "summary 要简短，写发生了什么、对用户意味着什么；不要逐句复述。"
            "facts 只保留以后还有用的事实；没有就给空数组。\n\n" + "\n".join(lines)
        )
        try:
            req = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=320, reason="memory_summary",
                response_format={"type": "json_object"},
                context={"system": "你是长期记忆整理器，只输出 JSON。", "user": prompt})
            raw = str(self.client.generate(req))
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            summary = str(data.get("summary") or "").strip()
            if not summary:
                return None
            facts = []
            for f in data.get("facts") or []:
                if isinstance(f, dict):
                    text = str(f.get("text") or "").strip()
                    key = str(f.get("key") or "").strip()
                    if text:
                        try:
                            days = float(f.get("expires_in_days") or 0.0)
                        except Exception:
                            days = 0.0
                        facts.append({"key": key or text[:24], "text": text, "expires_in_days": days})
                elif str(f).strip():
                    facts.append({"key": str(f)[:24], "text": str(f).strip()})
            return {"summary": summary[:700], "facts": facts[:8],
                    "open_loops": [str(x)[:80] for x in (data.get("open_loops") or [])][:5],
                    "closed_loops": [str(x)[:80] for x in (data.get("closed_loops") or [])][:5]}
        except Exception:
            return None


    def compact(self, rows, open_loops=None):
        """Two-model compaction: summary model + fact model, merged."""
        base = self(rows) or {}
        msgs = [str(getattr(e, "user", "")) for e in rows]
        extra = {}
        try:
            extra = self.extract_memory(msgs, open_loops or []) or {}
        except Exception:
            extra = {}
        facts = list(base.get("facts") or [])
        seen = {str(f.get("key") or "") for f in facts if isinstance(f, dict)}
        for f in extra.get("facts") or []:
            if isinstance(f, dict) and str(f.get("key") or "") not in seen:
                facts.append(f)
                seen.add(str(f.get("key") or ""))
        closed = list(base.get("closed_loops") or []) + list(extra.get("closed_loops") or [])
        return {"summary": str(base.get("summary") or ""), "facts": facts[:8],
                "open_loops": list(base.get("open_loops") or [])[:5],
                "closed_loops": closed[:5]}

    def extract_facts(self, messages):
        """LLM fact extraction for a small batch of recent user messages."""
        if self.client is None:
            return []
        texts = [str(x).strip() for x in messages if str(x).strip()]
        if not texts:
            return []
        prompt = (
            "从下面用户消息中提取长期有用的个人事实。只输出 JSON："
            "{\"facts\":[{\"key\":\"名字/宠物/工作/偏好/关系/事件\",\"text\":\"...\"}]}。"
            "只提取以后还有用的；不要提取一次性情绪、天气、客套话。没有就给空数组。\n\n"
            + "\n".join("- " + t[:300] for t in texts[-8:])
        )
        def parse(raw):
            data = json.loads(str(raw)[str(raw).find("{"):str(raw).rfind("}") + 1])
            out = []
            for f in data.get("facts") or []:
                if isinstance(f, dict):
                    text = str(f.get("text") or "").strip()
                    key = str(f.get("key") or "").strip()
                    if text:
                        try:
                            days = float(f.get("expires_in_days") or 0.0)
                        except Exception:
                            days = 0.0
                        out.append({"key": key or text[:24], "text": text, "expires_in_days": days})
            return out[:8]
        try:
            req = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=260, reason="memory_fact_extract",
                response_format={"type": "json_object"},
                context={"system": "你是长期事实抽取器，只输出 JSON。", "user": prompt})
            out = parse(self.fact_client.generate(req))
            if out:
                return out
        except Exception:
            pass
        # Retry without JSON mode if the first response was empty/invalid.
        try:
            req2 = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=260, reason="memory_fact_extract_retry",
                context={"system": "只输出 JSON，不要解释。", "user": prompt})
            return parse(self.fact_client.generate(req2))
        except Exception:
            return []

    def extract_memory(self, messages, open_loops=None):
        """Combined extraction: new facts + which existing open loops are closed."""
        if self.client is None:
            return {"facts": [], "closed_loops": []}
        texts = [str(x).strip() for x in messages if str(x).strip()]
        loops = [str(x) for x in (open_loops or []) if str(x).strip()]
        if not texts:
            return {"facts": [], "closed_loops": []}
        prompt = (
            "从下面用户消息中提取长期事实，并判断哪些已有未闭环事项已经解决。只输出 JSON："
            "{\"facts\":[{\"key\":\"名字/宠物/工作/偏好/关系/事件\",\"text\":\"...\","
            "\"expires_in_days\":0}],\"closed_loops\":[\"未闭环原文\"]}。"
            "只提取以后还有用的，不要把一次性情绪写成事实。"
            "只有消息明确表示已解决/已完成/已结束时才放进 closed_loops。"
            "\n\n已有未闭环：\n" + ("\n".join("- " + x for x in loops) if loops else "（无）")
            + "\n\n用户消息：\n" + "\n".join("- " + t[:300] for t in texts[-8:])
        )
        def parse(raw):
            data = json.loads(str(raw)[str(raw).find("{"):str(raw).rfind("}") + 1])
            facts = []
            for f in data.get("facts") or []:
                if isinstance(f, dict):
                    text = str(f.get("text") or "").strip()
                    key = str(f.get("key") or "").strip()
                    try:
                        days = float(f.get("expires_in_days") or 0.0)
                    except Exception:
                        days = 0.0
                    if text:
                        facts.append({"key": key or text[:24], "text": text, "expires_in_days": days})
            closed = [str(x).strip() for x in (data.get("closed_loops") or []) if str(x).strip()]
            return {"facts": facts[:8], "closed_loops": closed[:5]}
        try:
            req = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=320, reason="memory_extract",
                response_format={"type": "json_object"},
                context={"system": "你是长期记忆抽取器，只输出 JSON。", "user": prompt})
            out = parse(self.fact_client.generate(req))
            if out["facts"] or out["closed_loops"]:
                return out
        except Exception:
            pass
        try:
            req2 = __import__("mindbase.types", fromlist=["LLMRequest"]).LLMRequest(
                mode="semantic", max_tokens=320, reason="memory_extract_retry",
                context={"system": "只输出 JSON。", "user": prompt})
            return parse(self.fact_client.generate(req2))
        except Exception:
            return {"facts": [], "closed_loops": []}
