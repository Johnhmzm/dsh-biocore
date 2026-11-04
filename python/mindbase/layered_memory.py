from __future__ import annotations

import random
from typing import Dict, List, Optional

from .config import MindConfig
from .memory import MemoryItem, SparseMemory
from .noise import jaccard


class LayeredMemory:
    """Working buffer is elsewhere; this layer separates episodic and semantic stores.

    Episodic: time/context-bound traces.
    Semantic: high-importance or repeatedly retrieved regularities.
    Consolidation promotes episodic -> semantic; forgetting prunes weak episodes.
    """

    def __init__(self, cfg: MindConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.episodic = SparseMemory(cfg, rng)
        self.semantic = SparseMemory(cfg, rng)
        self.promoted = 0
        self.forgotten = 0
        self.forgotten_texts = set()

    @property
    def items(self) -> List[MemoryItem]:
        return list(self.semantic.items) + list(self.episodic.items)

    def set_affect(self, valence: float, arousal: float) -> None:
        self.episodic.set_affect(valence, arousal)
        self.semantic.set_affect(valence, arousal)

    def write(self, text: str, importance: float, rpe: float, now: float, valence: float = 0.0, arousal: float = 0.5) -> bool:
        ok = self.episodic.write(text, importance, rpe, now, valence=valence, arousal=arousal)
        if importance >= 0.62 or abs(rpe) >= 0.25:
            new_bigrams = {text[i:i + 2] for i in range(max(0, len(text) - 1))}
            if importance >= 0.80:
                keep = []
                for old in self.semantic.items:
                    if old.text != text and jaccard(old.bigrams, new_bigrams) >= 0.45:
                        self.forgotten += 1
                        self.forgotten_texts.add(old.text)
                        continue
                    keep.append(old)
                self.semantic.items = keep
            self.semantic.write(text, min(1.0, importance + 0.1), rpe, now, valence=valence, arousal=arousal)
            self.promoted += 1
        return ok

    def update_fact(self, text: str, now: float) -> None:
        new_bigrams = {text[i:i + 2] for i in range(max(0, len(text) - 1))}
        new_key = text.split("是", 1)[0] if "是" in text else ""
        keep = []
        for old in self.semantic.items:
            same_key = bool(new_key) and old.text.split("是", 1)[0] == new_key
            if old.text != text and (same_key or jaccard(old.bigrams, new_bigrams) >= 0.45):
                self.forgotten += 1
                self.forgotten_texts.add(old.text)
                continue
            keep.append(old)
        self.semantic.items = keep
        self.semantic.items.append(MemoryItem(text=text, importance=0.90, t=now, confidence=0.90, source="semantic"))
        self.forgotten_texts.discard(text)

    def _best(self, cue: str, now: float) -> Optional[MemoryItem]:
        items = self.retrieve_top_k(cue, now, k=5)
        if not items:
            return None
        return max(items, key=lambda x: x.importance * x.confidence)

    def retrieve(self, cue: str, now: float) -> Optional[MemoryItem]:
        return self._best(cue, now)

    def systems_consolidation(self, now: float, age_days: float = 1.0) -> int:
        moved = 0
        keep = []
        for item in self.episodic.items:
            if (now - item.t) > age_days * 86400 and item.text not in self.forgotten_texts:
                if all(x.text != item.text for x in self.semantic.items):
                    self.semantic.items.append(MemoryItem(text=item.text, importance=min(1.0, item.importance + 0.10), t=item.t, confidence=0.80, semanticized=True, source="semantic"))
                moved += 1
                continue
            keep.append(item)
        self.episodic.items = keep
        return moved

    def ambiguity(self, cue: str, now: float, k: int = 5) -> float:
        items = self.retrieve_top_k(cue, now, k=k)
        vals = sorted((x.importance * x.confidence for x in items), reverse=True)
        return round(vals[0] - vals[1], 4) if len(vals) > 1 else 1.0

    def resolve(self, cue: str, chosen: str, now: float, prediction_error: float = 1.0, threshold: float = 0.30) -> Dict[str, object]:
        if abs(float(prediction_error)) < float(threshold):
            return {"resolved": 0, "chosen": chosen, "reason": "low_prediction_error"}
        count = 0
        for item in self.semantic.items:
            if getattr(item, "semanticized", False) and item.text == chosen:
                return {"resolved": 0, "chosen": chosen, "reason": "semantic_memory_resists"}
            if item.text == chosen:
                item.confidence = 1.0
                # ensure it is recent relative to competing items
                item.last_access = now
                count += 1
            elif cue and cue in item.text:
                item.confidence = round(float(item.confidence) * 0.20, 4)
                count += 1
        return {"resolved": count, "chosen": chosen}

    def decide(self, cue: str, now: float, threshold: float = 0.05) -> Optional[dict]:
        items = self.retrieve_top_k(cue, now, k=5)
        if not items:
            return None
        ranked = sorted(items, key=lambda x: x.importance * x.confidence, reverse=True)
        gap = self.ambiguity(cue, now)
        if gap < threshold and len(ranked) > 1:
            return {"status": "uncertain", "candidates": [x.text for x in ranked[:3]], "ambiguity": gap}
        return {"status": "ok", "answer": ranked[0].text, "ambiguity": gap, "source": getattr(ranked[0], "source", "episodic"), "confidence": getattr(ranked[0], "confidence", 0.5)}

    def predict(self, cue: str, now: float) -> Optional[MemoryItem]:
        # semantic memory is the prior used before new evidence arrives
        return self.semantic.retrieve(cue, now) or self.episodic.retrieve(cue, now)

    def retrieve_top_k(self, cue: str, now: float, k: int = 3, threshold: float = 0.05) -> List[MemoryItem]:
        seen, out = set(), []
        for item in self.semantic.retrieve_top_k(cue, now, k, threshold) + self.episodic.retrieve_top_k(cue, now, k, threshold):
            if item.text not in seen:
                seen.add(item.text)
                out.append(item)
        out.sort(key=lambda x: x.importance, reverse=True)
        return out[:k]

    def retrieve_bm25(self, cue: str, now: float, k: int = 3) -> List[MemoryItem]:
        return self.retrieve_top_k(cue, now, k)

    def retrieve_embedding(self, cue: str, now: float, k: int = 3, client=None, chunk_chars: int = 0) -> List[MemoryItem]:
        return self.episodic.retrieve_embedding(cue, now, k, client, chunk_chars)

    def retrieve_hybrid(self, cue: str, now: float, k: int = 3, client=None, alpha: float = 0.6) -> List[MemoryItem]:
        return self.retrieve_top_k(cue, now, k)

    def consolidate(self) -> Dict[str, int]:
        promoted = 0
        for item in list(self.episodic.items):
            if item.text in self.forgotten_texts:
                continue
            if item.importance >= 0.60 or item.hits >= 2:
                if all(x.text != item.text for x in self.semantic.items):
                    self.semantic.write(item.text, min(1.0, item.importance + 0.05), 0.0, item.t)
                    promoted += 1
        # active forgetting: low-value, unaccessed episodes are pruned
        keep = []
        for item in self.episodic.items:
            if item.importance < 0.20 and item.hits == 0:
                self.forgotten += 1
                continue
            keep.append(item)
        self.episodic.items = keep
        self.promoted += promoted
        return {"promoted": promoted, "forgotten": self.forgotten}

    def snapshot(self) -> Dict[str, object]:
        return {
            "episodic": len(self.episodic.items),
            "semantic": len(self.semantic.items),
            "promoted": self.promoted,
            "forgotten": self.forgotten,
        }
