"""Bootstrap local appraisal heads from silver labels, then run without the LLM.

The LLM is optional and offline-only:
  texts -> DSH/LLM labeler -> StorySample dataset -> local linear AppraisalModel
Runtime _blend_appraisal() uses the local head; no LLM call.

The default ScriptedLabeler makes the pipeline testable without any API.
DSHLabeler is cache-friendly: frozen system prompt + appended JSON payload.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import MindConfig
from .engine import MindBase
from .lexicon import text_affect
from .teacher import ScriptedTeacher
from .training.appraisal import AppraisalModel, TARGETS
from .training.dataset import StorySample
from .training.features import feature_names
from .training.persist import load_heads, save_heads
from .types import Event

DATA = Path(__file__).resolve().parent / "data"
DEFAULT_HEADS = DATA / "bootstrap_heads.json"
DEFAULT_DATASET = DATA / "bootstrap_labels.jsonl"

_LABEL_SYSTEM = (
    "You are an annotation teacher for a digital companion. Return ONLY JSON. "
    "Rate the user text on five appraisal dimensions."
)


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except Exception:
        return lo
    return lo if x < lo else hi if x > hi else x


def _extract_json(text: str) -> Dict[str, object]:
    raw = str(text or "").strip()
    fence = chr(96) * 3
    if raw.startswith(fence):
        raw = raw.strip(chr(96))
        if raw.lower().startswith("json"):
            raw = raw[4:]
    i = raw.find("{")
    j = raw.rfind("}")
    if i >= 0 and j > i:
        raw = raw[i:j + 1]
    return json.loads(raw)


class ScriptedLabeler:
    """Deterministic silver labels; no network, no LLM. Used for tests/fallback."""

    SOCIAL_WORDS = ("你", "陪我", "谢谢", "喜欢", "想", "晚安", "在吗", "我们", "一起")

    def label(self, text: str, directed: bool = True, context: Optional[Dict[str, object]] = None) -> Dict[str, float]:
        v, a = text_affect(str(text or ""))
        social = 0.65 if directed or any(w in str(text or "") for w in self.SOCIAL_WORDS) else 0.35
        relevance = 0.70 if directed else 0.45
        threat = max(0.0, -float(v)) * 0.85
        return {
            "valence": round(_clamp(v, -1.0, 1.0), 4),
            "arousal": round(_clamp(a, 0.0, 1.0), 4),
            "threat": round(_clamp(threat, 0.0, 1.0), 4),
            "social": round(_clamp(social, 0.0, 1.0), 4),
            "relevance": round(_clamp(relevance, 0.0, 1.0), 4),
        }


class DSHLabeler:
    """LLM/DSH silver labeler with a stable cached prefix and scripted fallback."""

    def __init__(self, client=None, fallback: Optional[ScriptedLabeler] = None):
        self.client = client
        self.fallback = fallback or ScriptedLabeler()
        self.calls = 0

    def _call(self, system: str, user: str) -> str:
        if self.client is None:
            raise RuntimeError("no DSH/LLM client configured")
        from .llm_adapter import build_prompt
        from .types import LLMRequest
        req = LLMRequest(mode="semantic", urgency=0.5, max_tokens=300,
                         reason="bootstrap_labeler", context={"system": system, "user": user})
        self.calls += 1
        return str(self.client.generate(req))

    def label(self, text: str, directed: bool = True, context: Optional[Dict[str, object]] = None) -> Dict[str, float]:
        payload = json.dumps({
            "text": str(text or ""),
            "directed": bool(directed),
            "context": context or {},
            "schema": {k: "number" for k in TARGETS},
        }, ensure_ascii=False)
        try:
            data = _extract_json(self._call(_LABEL_SYSTEM, payload))
            out: Dict[str, float] = {}
            for k in TARGETS:
                lo, hi = (-1.0, 1.0) if k == "valence" else (0.0, 1.0)
                out[k] = round(_clamp(data.get(k, 0.0), lo, hi), 4)
            return out
        except Exception:
            return self.fallback.label(text, directed=directed, context=context)


def build_bootstrap_dataset(n_scenarios: int = 50, turns_per_scenario: int = 5,
                            labeler: Optional[object] = None, seed: int = 1,
                            mind_seed: int = 42) -> List[StorySample]:
    labeler = labeler or ScriptedLabeler()
    teacher = ScriptedTeacher(n_scenarios=max(1, int(n_scenarios)), seed=int(seed))
    mind = MindBase(MindConfig(seed=int(mind_seed)))
    samples: List[StorySample] = []
    for i in range(max(1, int(n_scenarios))):
        scenario = teacher.scenario(i)
        for turn in scenario.turns[:max(1, int(turns_per_scenario))]:
            event = Event(text=str(turn.text), speaker="user", directed=bool(turn.directed),
                          ts=mind.t + 60.0, domain="chat")
            try:
                mind.step(event)
            except Exception:
                pass
            state = mind._state_features()
            labels = labeler.label(str(turn.text), directed=bool(turn.directed),
                                   context={"persona": scenario.persona, "scenario": scenario.name})
            samples.append(StorySample(
                event=str(turn.text),
                state=dict(state),
                appraisal=dict(labels),
                action="speak",
                outcome={},
                reward=0.0,
                group=str(scenario.name),
                domain="bootstrap",
            ))
    return samples


def _split_by_group(samples: Sequence[StorySample], test_ratio: float = 0.25, seed: int = 0) -> Tuple[List[StorySample], List[StorySample]]:
    groups: Dict[str, List[StorySample]] = {}
    for s in samples:
        groups.setdefault(s.group or "default", []).append(s)
    keys = list(groups)
    random.Random(int(seed)).shuffle(keys)
    n_test = max(1, int(round(len(keys) * float(test_ratio)))) if keys else 0
    test_keys = set(keys[:n_test])
    train: List[StorySample] = []
    test: List[StorySample] = []
    for key, rows in groups.items():
        (test if key in test_keys else train).extend(rows)
    return train, test


def _mae(model: AppraisalModel, samples: Sequence[StorySample]) -> Dict[str, float]:
    if not samples:
        return {}
    err = {k: 0.0 for k in TARGETS}
    for s in samples:
        pred = model.predict(s.event, s.state)
        for k in TARGETS:
            err[k] += abs(float(pred.get(k, 0.0)) - float(s.appraisal.get(k, 0.0)))
    return {f"mae_{k}": round(v / len(samples), 4) for k, v in err.items()}


def run_bootstrap(n_scenarios: int = 50, turns_per_scenario: int = 5,
                  epochs: int = 120, seed: int = 1,
                  labeler: Optional[object] = None,
                  out_heads: Optional[Path | str] = None,
                  out_dataset: Optional[Path | str] = None,
                  verbose: bool = True) -> Dict[str, object]:
    t0 = time.perf_counter()
    labeler = labeler or ScriptedLabeler()
    samples = build_bootstrap_dataset(n_scenarios=n_scenarios, turns_per_scenario=turns_per_scenario,
                                      labeler=labeler, seed=seed)
    train, test = _split_by_group(samples, test_ratio=0.25, seed=seed)
    model = AppraisalModel(len(feature_names())).fit(train, epochs=int(epochs))
    metrics = {
        "n_total": len(samples),
        "n_train": len(train),
        "n_test": len(test),
        "epochs": int(epochs),
        "labeler": type(labeler).__name__,
        "train": _mae(model, train),
        "holdout": _mae(model, test),
        "elapsed_sec": round(time.perf_counter() - t0, 3),
    }
    hp = Path(out_heads) if out_heads else DEFAULT_HEADS
    dp = Path(out_dataset) if out_dataset else DEFAULT_DATASET
    hp.parent.mkdir(parents=True, exist_ok=True)
    save_heads({"appraisal": model}, hp)
    if out_dataset is not False:
        dp.parent.mkdir(parents=True, exist_ok=True)
        with dp.open("w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps({
                    "event": s.event, "state": s.state, "appraisal": s.appraisal,
                    "action": s.action, "group": s.group, "domain": s.domain,
                }, ensure_ascii=False) + "\n")
    metrics["heads_path"] = str(hp)
    metrics["dataset_path"] = str(dp)
    if verbose:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


def _build_dsh_client():
    try:
        from .llm_openai import OpenAICompatClient
    except Exception:
        return None
    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        return OpenAICompatClient(model=os.getenv("LLM_MODEL") or "deepseek-chat",
                                  base_url=os.getenv("LLM_BASE_URL") or "https://api.deepseek.com",
                                  api_key=key, temperature=0.2)
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap appraisal head from silver labels (LLM optional)")
    parser.add_argument("--scenarios", type=int, default=50)
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--teacher", choices=["scripted", "dsh"], default="scripted")
    parser.add_argument("--out-heads", default="")
    parser.add_argument("--out-dataset", default="")
    args = parser.parse_args()
    labeler = ScriptedLabeler()
    if args.teacher == "dsh":
        client = _build_dsh_client()
        labeler = DSHLabeler(client=client)
        if client is None:
            print("[bootstrap] DSH client unavailable; using scripted fallback.")
    run_bootstrap(n_scenarios=args.scenarios, turns_per_scenario=args.turns,
                  epochs=args.epochs, seed=args.seed, labeler=labeler,
                  out_heads=Path(args.out_heads) if args.out_heads else None,
                  out_dataset=Path(args.out_dataset) if args.out_dataset else None)


if __name__ == "__main__":
    main()
