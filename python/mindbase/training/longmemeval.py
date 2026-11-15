from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Sequence

from ..config import MindConfig
from ..bm25 import BM25
from ..memory import MemoryItem, SparseMemory

DATA = Path(__file__).resolve().parent.parent / "data" / "real" / "LongMemEval" / "longmemeval_oracle.json"


def load(path: Path | str | None = None) -> List[dict]:
    return json.loads(Path(path or DATA).read_text(encoding="utf-8"))


def _session_text(session: Sequence[dict]) -> str:
    return " ".join(f"{t.get('role', '')}: {t.get('content', '')}" for t in session)


def evaluate(limit: int = 30, k: int = 5, seed: int = 0, distractors: int = 0) -> Dict[str, float]:
    rows = load()[:limit]
    rng = random.Random(seed)
    pool: List[str] = []
    for row in rows:
        pool.extend(_session_text(s) for s in (row.get("haystack_sessions") or []))
    cfg = MindConfig()
    recall1 = recallk = mrr = 0.0
    base1 = basek = 0.0
    bm1 = bmk = 0.0
    for row in rows:
        sessions = row.get("haystack_sessions") or []
        ids = row.get("haystack_session_ids") or []
        targets = set(row.get("answer_session_ids") or [])
        if not sessions or not ids:
            continue
        texts = [_session_text(s) for s in sessions]
        if distractors:
            chosen = rng.sample(pool, min(distractors, len(pool)))
            texts = texts + chosen
            ids = list(ids) + [f"filler:{i}" for i in range(len(chosen))]
        mem = SparseMemory(cfg, random.Random(seed))
        mem.items = [MemoryItem(text=text, importance=0.5, t=float(i)) for i, text in enumerate(texts)]
        pos = {id(item): i for i, item in enumerate(mem.items)}
        top = mem.retrieve_top_k(row.get("question", ""), now=float(len(sessions) + 1), k=k, threshold=0.0)
        ranked = [ids[pos[id(item)]] for item in top]
        first = next((i + 1 for i, sid in enumerate(ranked) if sid in targets), 0)
        recall1 += int(first == 1)
        recallk += int(first > 0)
        mrr += 1.0 / first if first else 0.0
        last = ids[-k:]
        base1 += int(bool(last) and last[-1] in targets)
        basek += int(any(sid in targets for sid in last))
        bm = BM25(texts).rank(row.get("question", ""), k=k)
        bm_ids = [ids[i] for i in bm if i < len(ids)]
        bm1 += int(bool(bm_ids) and bm_ids[0] in targets)
        bmk += int(any(sid in targets for sid in bm_ids))
    n = max(1, len(rows))
    return {
        "n": len(rows),
        "recall@1": round(recall1 / n, 4),
        "recall@k": round(recallk / n, 4),
        "mrr": round(mrr / n, 4),
        "recency@1": round(base1 / n, 4),
        "recency@k": round(basek / n, 4),
        "bm25@1": round(bm1 / n, 4),
        "bm25@k": round(bmk / n, 4),
        "distractors": distractors,
    }


if __name__ == "__main__":
    print(evaluate())
