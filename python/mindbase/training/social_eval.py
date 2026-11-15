from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..llm_adapter import LLMClient
from ..types import LLMRequest


@dataclass
class SocialSample:
    source: str
    context: str
    question: str
    choices: Dict[str, str]
    answer: str

    def prompt(self) -> str:
        options = "\n".join(f"{k}. {v}" for k, v in sorted(self.choices.items()))
        return (
            "阅读情境与问题，从 A/B/C/D 中选一个最合适的答案。只回答字母。\n"
            f"情境：{self.context}\n问题：{self.question}\n{options}"
        )


def _get(row: Dict[str, object], keys: Sequence[str], default: str = "") -> str:
    for key in keys:
        if key in row and row[key] is not None:
            return str(row[key])
    return default


def load_tombench(path: Path | str) -> List[SocialSample]:
    path = Path(path)
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    out: List[SocialSample] = []
    for file in files:
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            choices = {
                "A": _get(row, ["OPTION-A", "选项A"]),
                "B": _get(row, ["OPTION-B", "选项B"]),
                "C": _get(row, ["OPTION-C", "选项C"]),
                "D": _get(row, ["OPTION-D", "选项D"]),
            }
            answer = _get(row, ["答案\nANSWER", "ANSWER", "答案"]).strip().upper()[:1]
            if not choices["A"] or answer not in choices:
                continue
            out.append(
                SocialSample(
                    source=file.stem,
                    context=_get(row, ["STORY", "故事"]),
                    question=_get(row, ["QUESTION", "问题"]),
                    choices=choices,
                    answer=answer,
                )
            )
    return out


def load_social_iqa(path: Path | str) -> List[SocialSample]:
    out: List[SocialSample] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        choices = {
            "A": str(row.get("answerA", "")),
            "B": str(row.get("answerB", "")),
            "C": str(row.get("answerC", "")),
        }
        label = str(row.get("label", "1")).strip().upper()
        answer = label if label in choices else {"1": "A", "2": "B", "3": "C"}.get(label, "A")
        if not choices["A"]:
            continue
        out.append(
            SocialSample(
                source="social_iqa",
                context=str(row.get("context", "")),
                question=str(row.get("question", "")),
                choices=choices,
                answer=answer,
            )
        )
    return out


class HeuristicChooser:
    """Token-overlap baseline; not a model."""

    def __call__(self, sample: SocialSample) -> str:
        context = set(sample.context) | set(sample.question)
        best, best_score = "A", -1
        for key, text in sample.choices.items():
            score = len(context & set(text))
            if score > best_score:
                best, best_score = key, score
        return best


class LLMChoicePredictor:
    def __init__(self, client: LLMClient):
        self.client = client

    def __call__(self, sample: SocialSample) -> str:
        request = LLMRequest(mode="reply", max_tokens=16, context={"last_user": sample.prompt()})
        reply = self.client.generate(request)
        match = re.search(r"[ABCD]", str(reply).upper())
        return match.group(0) if match else "A"


def evaluate(samples: Sequence[SocialSample], predictor) -> Dict[str, object]:
    if not samples:
        return {"n": 0, "accuracy": 0.0, "by_source": {}}
    hits = 0
    by_source: Dict[str, List[int]] = {}
    for sample in samples:
        pred = predictor(sample)
        ok = int(pred == sample.answer)
        hits += ok
        by_source.setdefault(sample.source, []).append(ok)
    return {
        "n": len(samples),
        "accuracy": round(hits / len(samples), 4),
        "by_source": {k: round(sum(v) / len(v), 4) for k, v in by_source.items()},
    }


def main() -> None:
    base = Path(__file__).resolve().parent.parent / "data"
    tom = base / "real" / "ToMBench"
    siqa = base / "samples" / "social_iqa_sample.jsonl"
    if tom.exists():
        samples = load_tombench(tom)
        print("ToMBench heuristic:", evaluate(samples, HeuristicChooser()))
    if siqa.exists():
        samples = load_social_iqa(siqa)
        print("Social IQa heuristic:", evaluate(samples, HeuristicChooser()))


if __name__ == "__main__":
    main()
