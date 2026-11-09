"""Long-term episodic memory: episodes -> consolidated notes -> recall."""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .memory_retriever import bm25_scores, rrf_fuse, mmr_select, TTLCache
from .vector_store import SQLiteVectorStore


def keywords(text: str) -> List[str]:
    t = re.sub(r"\s+", "", str(text or ""))
    grams = {t[i:i + 2] for i in range(max(0, len(t) - 1))}
    words = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[a-zA-Z]{2,}", str(text or "")))
    return list(grams | words)[:40]


@dataclass
class Episode:
    t: float
    user: str
    reply: str
    emotion: str
    importance: float
    valence: float
    keywords: List[str] = field(default_factory=list)


@dataclass
class Note:
    t: float
    summary: str
    keywords: List[str]
    importance: float
    valence: float
    key: str = ""
    strength: float = 1.0
    last_access: float = 0.0
    kind: str = "note"
    status: str = "open"
    expires_at: float = 0.0
    vec: List[float] = field(default_factory=list)
    arousal: float = 0.0
    salience: float = 0.0
    entropy: float = 0.0
    recall_count: int = 0


class LongTermMemory:
    def __init__(self, path: Path | str = "mindbase/data/longterm.json"):
        self.path = Path(path)
        self.episodes: List[Episode] = []
        self.notes: List[Note] = []
        self.facts: List[Note] = []
        self.loops: List[Note] = []
        self.forgotten: int = 0
        self.embedder = None
        self.embed_batch_size = 8
        self.embed_interval = 0.0
        self.min_similarity = 0.0
        self.memory_type_filter = "all"
        self.summary_trigger_rounds = 20
        self.vector_store = SQLiteVectorStore(self.path.with_suffix(".vectors.sqlite3"))
        self.cache = TTLCache(ttl=45.0, max_size=256)
        self.last_trace: Dict[str, float] = {}
        self._load()

    def _vid(self, note: Note) -> str:
        return str(note.kind) + ":" + str(int(float(note.t) * 1000))

    def _load_vectors(self) -> None:
        items = list(self.notes) + list(self.facts) + list(self.loops)
        ids = [self._vid(x) for x in items]
        stored = self.vector_store.get_many(ids)
        for item, key in zip(items, ids):
            if stored.get(key):
                item.vec = stored[key]
            elif getattr(item, "vec", None):
                self.vector_store.put(key, item.vec)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.episodes = [Episode(**x) for x in data.get("episodes", [])]
            self.notes = [Note(**x) for x in data.get("notes", [])]
            self.facts = [Note(**x) for x in data.get("facts", [])]
            self.loops = [Note(**x) for x in data.get("loops", [])]
            self.forgotten = int(data.get("forgotten", 0) or 0)
            self._load_vectors()
        except Exception:
            self.episodes, self.notes, self.facts = [], [], []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        def meta(x):
            d = dict(x.__dict__)
            d.pop("vec", None)
            return d
        data = {"episodes": [e.__dict__ for e in self.episodes[-300:]],
                "notes": [meta(n) for n in self.notes[-120:]],
                "facts": [meta(n) for n in self.facts[-80:]],
                "loops": [meta(n) for n in self.loops[-80:]],
                "forgotten": self.forgotten}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            active = [self._vid(x) for x in (self.notes + self.facts + self.loops)]
            self.vector_store.prune(active)
        except Exception:
            pass

    def add_episode(self, user: str, reply: str, emotion: str,
                    importance: float = 0.5, valence: float = 0.0) -> Episode:
        ep = Episode(t=time.time(), user=str(user)[:1200], reply=str(reply)[:1200],
                     emotion=str(emotion), importance=max(0.0, min(1.0, float(importance))),
                     valence=max(-1.0, min(1.0, float(valence))),
                     keywords=keywords(str(user) + " " + str(reply)))
        self.episodes.append(ep)
        self.episodes = self.episodes[-300:]
        self.cache.clear()
        self.save()
        return ep

    def extract_facts(self, text: str) -> List[str]:
        t = str(text or "")
        out = []
        for pat in (r"我叫([^\s，。,.！!？?]{1,12})", r"叫我([^\s，。,.！!？?]{1,12})",
                    r"我的名字是([^\s，。,.！!？?]{1,12})",
                    r"我喜欢([^\s，。,.！!？?]{1,20})",
                    r"我住在([^\s，。,.！!？?]{1,20})"):
            for m in re.finditer(pat, t):
                v = m.group(0).strip()
                if v not in out:
                    out.append(v)
        return out

    def attach_embedder(self, embedder) -> None:
        self.embedder = embedder

    def _ensure_vectors(self, items) -> None:
        if self.embedder is None:
            return
        missing = [x for x in items if not getattr(x, "vec", None)]
        if not missing:
            return
        dim = None
        try:
            for start_i in range(0, len(missing), max(1, self.embed_batch_size)):
                if start_i and float(getattr(self, "embed_interval", 0.0) or 0.0) > 0:
                    time.sleep(float(self.embed_interval))
                chunk = missing[start_i:start_i + max(1, self.embed_batch_size)]
                vecs = self.embedder.embed([x.summary for x in chunk])
                for item, vec in zip(chunk, vecs):
                    if not vec:
                        continue
                    vals = [float(x) for x in vec]
                    if not all(math.isfinite(x) for x in vals):
                        continue
                    if dim is None:
                        dim = len(vals)
                    if len(vals) != dim:
                        continue
                    item.vec = vals
                    self.vector_store.put(self._vid(item), vals)
        except Exception:
            pass

    def _fact_key(self, text: str) -> str:
        t = str(text or "")
        if re.search(r"我叫|叫我|我的名字是", t):
            return "name"
        if t.startswith("我喜欢"):
            return "likes:" + t[3:][:20]
        if t.startswith("我住在"):
            return "location"
        return "fact:" + t[:24]

    GENERIC_KEYS = {"", "事件", "事实", "其他", "其它", "event", "fact"}

    def add_fact(self, fact: str, key: str = "", expires_at: float = 0.0) -> Optional[Note]:
        text = str(fact or "").strip()
        if not text:
            return None
        key = str(key or "").strip()
        if key in self.GENERIC_KEYS:
            key = self._fact_key(text)
        for old in list(self.facts):
            if old.key == key:
                old.summary = text[:120]
                old.keywords = keywords(text)
                old.t = time.time()
                old.strength = min(1.0, old.strength + 0.2)
                old.last_access = time.time()
                old.expires_at = float(getattr(old, "expires_at", 0.0) or 0.0)
                old.vec = []
                self.cache.clear()
                self.save()
                return old
            if old.summary == text:
                return old
        now = time.time()
        note = Note(t=now, summary=text[:120], keywords=keywords(text),
                    importance=0.95, valence=0.0, key=key, strength=1.0,
                    last_access=now, kind="fact", expires_at=float(expires_at or 0.0))
        self.facts.append(note)
        self.facts = self.facts[-80:]
        self.cache.clear()
        self.save()
        return note

    def add_open_loop(self, text: str, key: str = "") -> Optional[Note]:
        text = str(text or "").strip()
        if not text:
            return None
        key = str(key or "").strip() or "loop:" + text[:24]
        for old in self.loops:
            if old.key == key or old.summary == text:
                old.summary = text[:160]
                old.status = "open"
                old.t = time.time()
                self.save()
                return old
        now = time.time()
        note = Note(t=now, summary=text[:160], keywords=keywords(text),
                    importance=0.85, valence=0.0, key=key, kind="loop", status="open")
        self.loops.append(note)
        self.loops = self.loops[-80:]
        self.cache.clear()
        self.save()
        return note

    def close_open_loop(self, key_or_text: str) -> bool:
        target = str(key_or_text or "").strip()
        if not target:
            return False
        for old in self.loops:
            if old.status == "open" and (old.key == target or target in old.summary or old.summary in target):
                old.status = "closed"
                old.t = time.time()
                self.cache.clear()
                self.save()
                return True
        return False

    CLOSE_CUES = ("过了", "通过", "完成", "搞定", "解决", "结束", "没事了", "已经", "结果出来", "放弃了")

    def close_matching(self, text: str) -> List[str]:
        """Close open loops mentioned in text when a closure cue is present."""
        t = str(text or "")
        if not t or not any(c in t for c in self.CLOSE_CUES):
            return []
        tt = set(keywords(t))
        if not tt:
            return []
        closed = []
        for old in self.loops:
            if old.status != "open":
                continue
            lt = set(keywords(old.summary))
            if not lt:
                continue
            overlap = len(tt & lt) / max(1, len(lt))
            if overlap >= 0.34 or any(len(x) >= 2 and x in t for x in lt):
                old.status = "closed"
                old.t = time.time()
                closed.append(old.summary)
        if closed:
            self.cache.clear()
            self.save()
        return closed

    def open_loops(self, limit: int = 5) -> List[Note]:
        return [x for x in self.loops if x.status == "open"][:max(1, int(limit))]

    def add_reflection(self, summary: str, self_insight: str = "",
                       emotional_arc: str = "", open_loops=None,
                       valence: float = 0.0) -> Optional[Note]:
        text = "反思：" + str(summary or "").strip()
        if not text.strip():
            return None
        if self_insight:
            text += " 我意识到：" + str(self_insight).strip()
        if emotional_arc:
            text += " 情绪轨迹：" + str(emotional_arc).strip()
        now = time.time()
        note = Note(t=now, summary=text[:700], keywords=keywords(text),
                    importance=0.82, valence=float(valence or 0.0),
                    kind="reflection", strength=0.9, last_access=now)
        self.notes.append(note)
        self.notes = self.notes[-140:]
        for item in open_loops or []:
            try:
                self.add_open_loop(str(item))
            except Exception:
                pass
        self._ensure_vectors([note])
        self.cache.clear()
        self.save()
        return note

    def retention(self, note: Note, now: Optional[float] = None) -> float:
        now = float(time.time() if now is None else now)
        age_days = max(0.0, now - float(note.t)) / 86400.0
        tau = 30.0 + 60.0 * max(0.0, min(1.0, float(note.strength)))
        return math.exp(-age_days / tau)

    def forget(self, now: Optional[float] = None) -> int:
        now = float(time.time() if now is None else now)
        expired = [x for x in self.facts if x.expires_at and x.expires_at < now]
        if expired:
            self.facts = [x for x in self.facts if not (x.expires_at and x.expires_at < now)]
            self.cache.clear()
        keep, dropped = [], 0
        for n in self.notes:
            if self.retention(n, now) * 0.5 + n.importance * 0.5 < 0.14:
                dropped += 1
            else:
                keep.append(n)
        self.notes = keep
        self.forgotten += dropped
        if dropped:
            self.save()
        return dropped

    def consolidate(self, cluster: int = 12, max_notes: int = 120, summarizer=None) -> Optional[Note]:
        rows = self.episodes[-int(cluster):]
        if len(rows) < 3:
            return None
        data = None
        if summarizer is not None:
            try:
                if hasattr(summarizer, "compact"):
                    data = summarizer.compact(rows)
                else:
                    data = summarizer(rows)
            except Exception:
                data = None
        if data and str(data.get("summary") or "").strip():
            summary = str(data.get("summary")).strip()
            loops = [str(x) for x in (data.get("open_loops") or []) if str(x).strip()]
            if loops:
                summary += "；未闭环：" + " / ".join(loops[:3])
            for item in data.get("facts") or []:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    key = str(item.get("key") or "").strip()
                else:
                    text, key = str(item).strip(), ""
                if text:
                    days = float(item.get("expires_in_days") or 0.0) if isinstance(item, dict) else 0.0
                    self.add_fact(text, key=key,
                                  expires_at=time.time() + days * 86400 if days > 0 else 0.0)
            for item in data.get("open_loops") or []:
                self.add_open_loop(str(item))
            for item in data.get("closed_loops") or []:
                self.close_open_loop(str(item))
        else:
            user_text = " / ".join(e.user[:80] for e in rows if e.user)
            summary = "那段时间：" + user_text[-400:]
        kw = keywords(summary)
        importance = max(e.importance for e in rows)
        valence = sum(e.valence for e in rows) / len(rows)
        strength = min(1.0, 0.55 * importance + 0.30 * abs(valence) + 0.15 * min(1.0, len(rows) / 12.0))
        for old in self.notes:
            inter = len(set(kw) & set(old.keywords))
            union = max(1, len(set(kw) | set(old.keywords)))
            if inter / union > 0.65:
                old.summary = (old.summary + " / " + summary[-200:])[-700:]
                old.keywords = list(dict.fromkeys(old.keywords + kw))[:80]
                old.importance = max(old.importance, importance)
                old.valence = 0.5 * (old.valence + valence)
                old.strength = min(1.0, max(old.strength, strength) + 0.1)
                old.t = time.time()
                old.last_access = time.time()
                old.expires_at = float(getattr(old, "expires_at", 0.0) or 0.0)
                old.vec = []
                self.cache.clear()
                self.save()
                return old
        now = time.time()
        note = Note(t=now, summary=summary[:600], keywords=kw,
                    importance=round(importance, 3), valence=round(valence, 3),
                    strength=round(strength, 3), last_access=now)
        self._ensure_vectors([note])
        self.notes.append(note)
        self.notes = self.notes[-max_notes:]
        self.forget()
        self.cache.clear()
        self.save()
        return note



    def _order_oldest(self, rows, query: str = "", clusters: int = 6):
        """Embedding-cluster the oldest rows, then rerank cluster reps."""
        rows = list(rows)
        if not rows:
            return []
        try:
            from .embedding_rerank import cosine
            emb = getattr(self, "embedder", None)
            texts = [(str(getattr(e, "user", "")) + " " + str(getattr(e, "reply", "")))[:240] for e in rows]
            vecs = emb.embed(texts, timeout=20.0) if emb is not None and getattr(emb, "embed_enabled", False) else []
        except Exception:
            vecs = []
        if not vecs or len(vecs) != len(rows):
            return rows
        groups = []
        for i, v in enumerate(vecs):
            best, best_s = -1, -1.0
            for gi, g in enumerate(groups):
                s = cosine(v, vecs[g[0]])
                if s > best_s:
                    best, best_s = gi, s
            if best >= 0 and best_s >= 0.72 and len(groups[best]) < 6:
                groups[best].append(i)
            else:
                groups.append([i])
        groups = groups[:max(1, int(clusters))]
        reps = [g[0] for g in groups]
        try:
            if query and getattr(self.embedder, "rerank_enabled", False):
                rr = self.embedder.rerank(query, [texts[i] for i in reps], top_n=len(reps))
                score = {int(i): float(s) for i, s in (rr or [])}
                groups.sort(key=lambda g: -score.get(reps.index(g[0]), 0.0))
        except Exception:
            pass
        return [rows[i] for g in groups for i in g]

    def compact_oldest(self, threshold_chars: int = 6000, batch: int = 24, summarizer=None, query: str = "") -> Optional[Note]:
        """Summarize the oldest episodes only after accumulated text exceeds threshold."""
        try:
            episodes = list(self.episodes)
            total = sum(len(str(getattr(e, "user", ""))) + len(str(getattr(e, "reply", "")))
                        for e in episodes)
        except Exception:
            return None
        if total < int(threshold_chars) or len(episodes) < 3:
            return None
        rows = episodes[:max(3, int(batch))]
        data = None
        if summarizer is not None:
            try:
                data = summarizer(rows)
            except Exception:
                data = None
        if data and str(data.get("summary") or "").strip():
            summary = str(data.get("summary")).strip()
            loops = [str(x) for x in (data.get("open_loops") or []) if str(x).strip()]
            if loops:
                summary += "；未闭环：" + " / ".join(loops[:3])
            for item in data.get("facts") or []:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    key = str(item.get("key") or "").strip()
                    days = float(item.get("expires_in_days") or 0.0)
                else:
                    text, key, days = str(item).strip(), "", 0.0
                if text:
                    self.add_fact(text, key=key,
                                  expires_at=time.time() + days * 86400 if days > 0 else 0.0)
            for item in data.get("open_loops") or []:
                self.add_open_loop(str(item))
            for item in data.get("closed_loops") or []:
                self.close_open_loop(str(item))
        else:
            summary = "较早的对话：" + " / ".join(e.user[:80] for e in rows if e.user)[-400:]
        kw = keywords(summary)
        importance = max(e.importance for e in rows)
        valence = sum(e.valence for e in rows) / len(rows)
        strength = min(1.0, 0.55 * importance + 0.30 * abs(valence) + 0.15 * min(1.0, len(rows) / 12.0))
        now = time.time()
        note = Note(t=now, summary=summary[:600], keywords=kw,
                    importance=round(importance, 3), valence=round(valence, 3),
                    strength=round(strength, 3), last_access=now)
        self._ensure_vectors([note])
        self.notes.append(note)
        self.notes = self.notes[-120:]
        self.episodes = episodes[len(rows):]
        self.cache.clear()
        self.save()
        return note

    def recall(self, query: str, affect: Optional[Dict[str, float]] = None, k: int = 3,
               recent_context: str = "") -> str:
        q = str(query or "")
        if not q.strip():
            return ""
        # query expansion: only for short / anaphoric asks, like "那个事呢"
        expanded = q
        if recent_context and (len(q) <= 10 or any(w in q for w in ("那个", "上次", "之前", "后来", "它", "这事"))):
            expanded = (q + " " + str(recent_context)[-220:]).strip()
        av = round(float((affect or {}).get("v", 0.0)), 2)
        ck = (expanded, av, int(k), self.memory_type_filter, self.min_similarity)
        cached = self.cache.get(ck)
        if cached is not None:
            self.last_trace = dict(self.cache.get(ck + ("trace",)) or {})
            return str(cached)
        now_ts = time.time()
        facts = [] if self.memory_type_filter == "event_only" else [
            x for x in self.facts if not (x.expires_at and x.expires_at < now_ts)]
        notes = list(self.notes)
        items = facts + notes
        if not items:
            return ""
        texts = [x.summary for x in items]
        qvec = None
        try:
            if self.embedder is not None:
                # Small stores can be embedded inline; large legacy stores use BM25.
                if len(items) <= 2:
                    self._ensure_vectors(items)
                qvec = self.embedder.embed([expanded], timeout=5.0)[0]
        except Exception:
            qvec = None
        bm = bm25_scores(texts, expanded)
        rankings = [sorted(range(len(items)), key=lambda i: (-bm[i], i))]
        if qvec is not None:
            vec_scores = []
            try:
                from .embedding_rerank import cosine
                for it in items:
                    vec_scores.append(max(0.0, cosine(qvec, it.vec)) if it.vec else 0.0)
            except Exception:
                vec_scores = [0.0] * len(items)
            rankings.append(sorted(range(len(items)), key=lambda i: (-vec_scores[i], i)))
        fused = rrf_fuse(rankings, k=60)
        now = time.time()
        recency = [self.retention(x, now) for x in items]
        max_rrf = max(fused.values()) if fused else 1.0
        base_scores = []
        for i, it in enumerate(items):
            rel = fused.get(i, 0.0) / max(1e-9, max_rrf)
            affect_bonus = 0.0
            if affect:
                affect_bonus = max(0.0, 1.0 - abs(float(affect.get("v", 0.0)) - float(it.valence)))
            salience = min(1.0, 0.5 * abs(float(it.valence)) + 0.5 * float(it.importance))
            base_scores.append(0.44 * rel + 0.20 * float(it.importance) + 0.14 * recency[i]
                               + 0.22 * affect_bonus + 0.08 * salience - 0.04 * float(it.entropy))
        order = sorted(range(len(items)), key=lambda i: (-base_scores[i], i))
        candidates = order[:20]
        if self.embedder is not None and getattr(self.embedder, "rerank_enabled", False) and len(candidates) > 1:
            try:
                rr = self.embedder.rerank(expanded, [texts[i] for i in candidates], top_n=len(candidates))
                if rr:
                    vals = [float(s) for _, s in rr]
                    lo, hi = min(vals), max(vals)
                    for idx, score in rr:
                        if 0 <= idx < len(candidates):
                            orig = candidates[idx]
                            norm = 1.0 if hi <= lo else (float(score) - lo) / (hi - lo)
                            base_scores[orig] = (0.44 * norm + 0.20 * float(items[orig].importance)
                                                 + 0.14 * recency[orig] + 0.22 * base_scores[orig])
                    order = sorted(candidates, key=lambda i: (-base_scores[i], i))
            except Exception:
                pass
        top_scores = [base_scores[i] for i in order]
        chosen_idx = mmr_select([texts[i] for i in order], top_scores, max(1, int(k)), lam=0.7)
        picked = [order[i] for i in chosen_idx]
        # recent memory forced retention: keep one recent item if not already selected
        recent_cut = now - 72 * 3600
        recent_items = [i for i in order if items[i].t >= recent_cut and i not in picked]
        if recent_items:
            if len(picked) >= max(1, int(k)):
                picked[-1] = recent_items[0]
            else:
                picked.append(recent_items[0])
        if not picked:
            self.last_trace = {}
            return ""
        weights = [max(1e-6, float(base_scores[i])) for i in picked]
        wsum = sum(weights) or 1.0
        chosen = [items[i] for i in picked]
        memory_valence = sum(w * float(x.valence) for w, x in zip(weights, chosen)) / wsum
        importance = max(float(x.importance) for x in chosen)
        negative_ratio = sum(w for w, x in zip(weights, chosen) if float(x.valence) < 0) / wsum
        arousal = min(1.0, abs(memory_valence) * importance * 1.4 + 0.15 * importance)
        open_loops = len([x for x in self.loops if x.status == "open"])
        salience = min(1.0, 0.5 * abs(memory_valence) + 0.5 * importance)
        self.last_trace = {"valence": round(memory_valence, 4), "arousal": round(arousal, 4),
                           "importance": round(importance, 4), "negative_ratio": round(negative_ratio, 4),
                           "salience": round(salience, 4), "open_loops": int(open_loops),
                           "items": len(chosen)}
        for i in picked:
            items[i].last_access = now
            try:
                items[i].recall_count = int(getattr(items[i], "recall_count", 0)) + 1
            except Exception:
                pass
        out = "\n".join(dict.fromkeys(items[i].summary for i in picked))
        self.cache.set(ck, out)
        self.cache.set(ck + ("trace",), self.last_trace)
        return out

    def stats(self) -> Dict[str, object]:
        return {"episodes": len(self.episodes), "notes": len(self.notes), "facts": len(self.facts),
                "forgotten": self.forgotten,
                "expired": len([x for x in self.facts if x.expires_at and x.expires_at < time.time()]),
                "loops": len([x for x in self.loops if x.status == "open"]),
                "vectors": self.vector_store.count(), "cache": self.cache.stats()}
