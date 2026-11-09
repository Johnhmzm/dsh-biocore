from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .llm_adapter import LLMAdapter, LLMClient
from .noise import clip
from .types import Event

DATA_PATH = Path(__file__).with_name("data") / "exusiai_stories.json"


@dataclass
class StoryLine:
    text: str
    tags: List[str] = field(default_factory=list)
    heldout: bool = False


@dataclass
class StoryCorpus:
    train: List[StoryLine]
    heldout: List[StoryLine]

    @classmethod
    def load(cls, path: Path | None = None) -> "StoryCorpus":
        raw = json.loads(Path(path or DATA_PATH).read_text(encoding="utf-8"))
        return cls(
            train=[StoryLine(**x) for x in raw.get("train", [])],
            heldout=[StoryLine(**x, heldout=True) for x in raw.get("heldout", [])],
        )


@dataclass
class StoryResult:
    line: StoryLine
    reply: str
    score: float
    accepted: bool
    identity: float
    mood: float
    values: Dict[str, float]
    mb: Dict[str, object]


class RuleJudge:
    """Cheap style judge: no model, just observable rules."""

    FORBIDDEN = ("作为ai", "作为一个", "表达层", "上下文", "mood=", "identity", "json", "我不能", "无法")
    MARKERS = ("老板", "派对", "苹果派", "啊", "呀", "嘛", "哈", "嘿", "啦", "吧", "呢", "哦", "走", "来", "玩", "笑", "好耶", "！", "~")

    def score(self, reply: str, line: str = "") -> float:
        if not reply or not reply.strip():
            return 0.0
        text = reply.strip()
        s = 0.50
        if len(text) > 100:
            s -= 0.20
        if any(bad in text.lower() for bad in self.FORBIDDEN):
            s -= 0.50
        if any(marker in text for marker in self.MARKERS):
            s += 0.20
        if line and text == line.strip():
            s -= 0.20
        return round(clip(s, 0.0, 1.0), 4)

    def feedback(self, score: float) -> Optional[float]:
        if score >= 0.60:
            return round(clip((score - 0.50) * 2.0, 0.0, 1.0), 4)
        return None


class StoryLoop:
    """Story line -> event -> reply -> judge -> only good variants learn."""

    def __init__(
        self,
        mind,
        adapter: LLMAdapter,
        corpus: StoryCorpus,
        judge: Optional[RuleJudge] = None,
        min_score: float = 0.55,
        consolidate: bool = True,
    ):
        self.mind = mind
        self.adapter = adapter
        self.corpus = corpus
        self.judge = judge or RuleJudge()
        self.min_score = min_score
        self.consolidate = consolidate
        self.before = mind.snapshot()

    def run(self) -> List[StoryResult]:
        results: List[StoryResult] = []
        for i, line in enumerate(self.corpus.train):
            box = {"score": 0.0}

            def feedback_fn(event, reply, _line=line, _box=box):
                score = self.judge.score(reply, _line.text)
                _box["score"] = score
                return self.judge.feedback(score) if score >= self.min_score else None

            turn = self.adapter.handle(
                Event(text=line.text, speaker="博士", directed=True, ts=float(i * 60)),
                feedback_fn=feedback_fn,
            )
            score = box["score"]
            results.append(
                StoryResult(
                    line=line,
                    reply=turn.reply or "",
                    score=score,
                    accepted=score >= self.min_score,
                    identity=float(self.mind.self_model.identity),
                    mood=float(self.mind.affect.mood),
                    values=self.mind.values.snapshot(),
                    mb=dict(self.mind.mb.snapshot()),
                )
            )
        if self.consolidate:
            self.mind.learner.consolidate()
        return results

    def heldout(self) -> List[StoryResult]:
        results: List[StoryResult] = []
        for i, line in enumerate(self.corpus.heldout):
            turn = self.adapter.handle(
                Event(text=line.text, speaker="博士", directed=True, ts=float((len(self.corpus.train) + i) * 60)),
                feedback_fn=None,
            )
            reply = turn.reply or ""
            score = self.judge.score(reply, line.text)
            results.append(
                StoryResult(
                    line=line,
                    reply=reply,
                    score=score,
                    accepted=score >= self.min_score,
                    identity=float(self.mind.self_model.identity),
                    mood=float(self.mind.affect.mood),
                    values=self.mind.values.snapshot(),
                    mb=dict(self.mind.mb.snapshot()),
                )
            )
        return results

    def summary(self, results: Sequence[StoryResult], heldout: Optional[Sequence[StoryResult]] = None) -> Dict[str, object]:
        accepted = [r for r in results if r.accepted]
        after = self.mind.snapshot()
        values_now = self.mind.values.snapshot()
        values_before = self.before.get("values", {})
        values_delta = {
            k: round(float(values_now[k]) - float(values_before.get(k, 0.0)), 4)
            for k in values_now
        }
        return {
            "n": len(results),
            "accepted": len(accepted),
            "acceptance_rate": round(len(accepted) / max(1, len(results)), 3),
            "mean_score": round(sum(r.score for r in results) / max(1, len(results)), 3),
            "heldout_mean": round(sum(r.score for r in (heldout or [])) / max(1, len(heldout or [])), 3) if heldout else None,
            "heldout_acceptance": round(sum(1 for r in (heldout or []) if r.accepted) / max(1, len(heldout or [])), 3) if heldout else None,
            "identity_before": self.before.get("self", {}).get("identity"),
            "identity_after": after.get("self", {}).get("identity"),
            "values_delta": values_delta,
            "learning_replay": len(self.mind.learner.replay),
        }


def noise_sweep(
    mind_factory: Callable[[], object],
    client_factory: Callable[[float], LLMClient],
    corpus: StoryCorpus,
    temperatures: Sequence[float] = (0.5, 0.8, 1.1),
    judge: Optional[RuleJudge] = None,
) -> Dict[str, Dict[str, object]]:
    out: Dict[str, Dict[str, object]] = {}
    for temp in temperatures:
        mind = mind_factory()
        adapter = LLMAdapter(mind, client_factory(float(temp)))
        loop = StoryLoop(mind, adapter, corpus, judge=judge)
        results = loop.run()
        held = loop.heldout()
        out[f"{temp:.1f}"] = loop.summary(results, held)
    return out


def compare_clients(
    mind_factory: Callable[[], object],
    clients: Dict[str, LLMClient],
    corpus: StoryCorpus,
    judge: Optional[RuleJudge] = None,
) -> Dict[str, Dict[str, object]]:
    out: Dict[str, Dict[str, object]] = {}
    for name, client in clients.items():
        mind = mind_factory()
        loop = StoryLoop(mind, LLMAdapter(mind, client), corpus, judge=judge)
        results = loop.run()
        held = loop.heldout()
        out[name] = loop.summary(results, held)
    return out
