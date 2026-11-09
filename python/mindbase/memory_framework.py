"""Unified memory framework.

Layers:
  working memory  -> MemoryStore (recent diary/impressions)
  episodic        -> LongTermMemory.episodes
  semantic/fact   -> LongTermMemory.facts
  consolidated    -> LongTermMemory.notes
  vectors         -> SQLite sidecar

Read: working + long-term hybrid recall.
Write: episode + fact extraction + consolidation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Dict, Optional

from .longterm_memory import LongTermMemory
from .scoped_identity import PublicDigest


@dataclass
class MemoryConfig:
    working_limit: int = 4
    recall_k: int = 3
    summary_trigger_rounds: int = 20
    fact_extract_interval: int = 5


@dataclass
class MemoryContext:
    working: str = ""
    long_term: str = ""
    facts: list = field(default_factory=list)
    open_loops: list = field(default_factory=list)
    public_digest: str = ""
    trace: dict = field(default_factory=dict)
    query: str = ""

    def as_text(self) -> str:
        parts = []
        if self.working:
            parts.append("最近：\n" + self.working)
        if self.long_term:
            parts.append("长期记忆：\n" + self.long_term)
        if self.open_loops:
            parts.append("未闭环：\n" + "\n".join("- " + str(x) for x in self.open_loops))
        if self.public_digest:
            parts.append("群公共记忆：\n" + self.public_digest)
        return "\n".join(parts)


class MemoryFramework:
    def __init__(self, working_store, longterm: LongTermMemory,
                 user_id: str = "web", config: Optional[MemoryConfig] = None,
                 summarizer=None, digest: Optional[PublicDigest] = None, scope: str = "private"):
        self.working = working_store
        self.longterm = longterm
        self.user_id = str(user_id or "web")
        self.cfg = config or MemoryConfig()
        self.summarizer = summarizer
        self.digest = digest
        self.scope = str(scope or "private")
        self.turns = 0
        self._pending_texts = []

    def recall(self, query: str, affect_v: float = 0.0,
               recent_context: str = "", k: Optional[int] = None) -> MemoryContext:
        working = ""
        try:
            working = self.working.recall(self.user_id, limit=self.cfg.working_limit)
        except Exception:
            working = ""
        ltm = ""
        try:
            ltm = self.longterm.recall(
                str(query or ""), {"v": float(affect_v)}, k=int(k or self.cfg.recall_k),
                recent_context=recent_context)
        except Exception:
            ltm = ""
        facts = [x.summary for x in self.longterm.facts[-5:]]
        loops = [x.summary for x in self.longterm.open_loops(5)]
        trace = dict(getattr(self.longterm, "last_trace", {}) or {})
        digest_text = ""
        if self.digest is not None and self.scope == "private":
            try:
                digest_text = self.digest.read("", limit=3)
            except Exception:
                digest_text = ""
        return MemoryContext(working=working, long_term=ltm, facts=facts, open_loops=loops,
                             public_digest=digest_text, trace=trace, query=str(query or ""))

    def observe(self, text: str, reply: str, emotion: str,
                affect_v: float = 0.0, reward: float = 0.5) -> Dict[str, object]:
        self.turns += 1
        out: Dict[str, object] = {"episode": False, "facts": 0, "llm_facts": 0,
                                  "consolidated": False, "closed_loops": [], "llm_closed": []}
        try:
            self.longterm.add_episode(str(text), str(reply), str(emotion),
                                      importance=max(0.3, float(reward)),
                                      valence=float(affect_v))
            out["episode"] = True
        except Exception:
            pass
        try:
            for fact in self.longterm.extract_facts(str(text)):
                self.longterm.add_fact(fact)
                out["facts"] = int(out["facts"]) + 1
        except Exception:
            pass
        # periodic LLM fact extraction: regex above is the immediate fallback
        try:
            self._pending_texts.append(str(text))
            self._pending_texts = self._pending_texts[-8:]
            interval = max(1, int(self.cfg.fact_extract_interval))
            if self.summarizer is not None and self.turns % interval == 0:
                extracted = []
                if hasattr(self.summarizer, "extract_memory"):
                    loops = [x.summary for x in self.longterm.open_loops(5)]
                    res = self.summarizer.extract_memory(self._pending_texts, loops or [])
                    extracted = list((res or {}).get("facts") or [])
                    for name in (res or {}).get("closed_loops") or []:
                        if self.longterm.close_open_loop(str(name)):
                            out["llm_closed"] = list(out["llm_closed"]) + [str(name)]
                elif hasattr(self.summarizer, "extract_facts"):
                    extracted = self.summarizer.extract_facts(self._pending_texts) or []
                for f in extracted:
                    days = float(f.get("expires_in_days") or 0.0)
                    self.longterm.add_fact(str(f.get("text") or ""), key=str(f.get("key") or ""),
                                           expires_at=time.time() + days * 86400 if days > 0 else 0.0)
                    out["llm_facts"] = int(out["llm_facts"]) + 1
                self._pending_texts = []
        except Exception:
            pass
        try:
            out["closed_loops"] = self.longterm.close_matching(str(text))
        except Exception:
            out["closed_loops"] = []
        if self.turns % max(1, int(self.cfg.summary_trigger_rounds)) == 0:
            try:
                self.longterm.consolidate(summarizer=self.summarizer)
                out["consolidated"] = True
            except Exception:
                pass
        return out

    def consolidate_now(self):
        try:
            return self.longterm.consolidate(summarizer=self.summarizer)
        except Exception:
            return None

    def stats(self) -> Dict[str, object]:
        st = dict(self.longterm.stats())
        st["turns"] = self.turns
        st["user_id"] = self.user_id
        return st
