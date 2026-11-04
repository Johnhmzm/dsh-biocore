"""Teacher / simulated environment interfaces for offline evolution.

The base is the learner.  The teacher only generates scenarios and scores
outcomes; it never changes the base's state directly.  Two implementations:

- ScriptedTeacher: deterministic, no network, used for smoke tests and fast
  offline evolution runs.
- DSHTeacher: uses a DSH/LLM client to generate scenarios and judge turns.
  It is a teacher/judge, not the base's controller.  No human labels needed.

This module deliberately contains no hand-written *base* decision rules.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence


@dataclass
class Turn:
    text: str
    directed: bool = False
    expected: str = "any"  # speak | silence | any
    valence: float = 0.0
    topic: str = "chat"


@dataclass
class Scenario:
    name: str
    persona: str = "user"
    goal: str = "chat"
    turns: List[Turn] = field(default_factory=list)


class Teacher(Protocol):
    def scenario(self, seed: int) -> Scenario: ...
    def feedback(self, scenario: Scenario, turn: Turn, action: str, mind) -> float: ...


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except Exception:
        return lo
    return lo if x < lo else hi if x > hi else x


def _extract_json(text: str) -> Dict[str, object]:
    """Best-effort JSON object extraction from an LLM answer."""
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


class ScriptedTeacher:
    """Deterministic teacher/environment, no network and no human labels.

    The reward is an explicit simulated outcome: whether the base action
    matched the turn's social affordance, plus a small physiological term.
    It is training *signal*, not a decision rule for the base.
    """

    TEXT_POOL = [
        "在吗",
        "今天有点累",
        "老板骂我了",
        "陪我聊会儿",
        "我失恋了",
        "今天天气不错",
        "帮我记住我喜欢猫",
        "你在干嘛",
        "晚安",
        "我升职了",
        "有点烦",
        "你会一直陪着我吗",
        "我刚刚做噩梦了",
        "谢谢",
        "周末去哪玩好",
        "我想听你说话",
        "我好像感冒了",
        "没事，就是有点想你",
        "你睡了吗",
        "我今天一个人吃饭",
    ]

    def __init__(self, n_scenarios: int = 50, seed: int = 1, turns_per_scenario: int = 5):
        self.n_scenarios = max(1, int(n_scenarios))
        self.seed = int(seed)
        self.turns_per_scenario = max(1, int(turns_per_scenario))
        self.scenarios: List[Scenario] = self._build(self.n_scenarios, self.seed)

    def _build(self, n: int, seed: int) -> List[Scenario]:
        rng = random.Random(seed)
        personas = ["老友", "同事", "难过的朋友", "陌生人", "熟悉的用户"]
        out: List[Scenario] = []
        for i in range(n):
            persona = personas[i % len(personas)]
            turns: List[Turn] = []
            for j in range(self.turns_per_scenario):
                text = rng.choice(self.TEXT_POOL)
                directed = (j % 3) != 2
                if directed:
                    expected = "speak" if rng.random() < 0.82 else "silence"
                else:
                    expected = "silence" if rng.random() < 0.55 else "any"
                turns.append(Turn(text=text, directed=directed, expected=expected,
                                  valence=0.0, topic="chat"))
            out.append(Scenario(name="scripted_%02d" % i, persona=persona, goal="support", turns=turns))
        return out

    def scenario(self, seed: int) -> Scenario:
        return self.scenarios[int(seed) % len(self.scenarios)]

    def feedback(self, scenario: Scenario, turn: Turn, action: str, mind) -> float:
        expected = str(turn.expected or "any")
        if expected == "any":
            match = 0.2
        elif expected == "speak" and str(action) in ("speak", "ask"):
            match = 1.0
        elif expected == "silence" and str(action) == "silence":
            match = 1.0
        elif expected == "speak" and str(action) == "silence":
            match = -1.0
        elif expected == "silence" and str(action) in ("speak", "ask"):
            match = -1.0
        else:
            match = 0.0
        try:
            v = float(getattr(mind.affect, "v", 0.0))
            stress = float(getattr(mind.affect, "stress", 0.0))
            viability = float(mind.viability_state.get("viability", 1.0))
        except Exception:
            v, stress, viability = 0.0, 0.0, 1.0
        reward = 0.6 * match + 0.2 * (viability - 0.5) + 0.1 * v - 0.2 * stress
        return _clamp(reward, -1.0, 1.0)


_SCENARIO_SYSTEM = (
    "You are a teacher for a digital companion base. Return ONLY JSON. "
    "Generate one realistic chat scenario for offline training."
)

_FEEDBACK_SYSTEM = (
    "You are an evaluation teacher for a digital companion. Return ONLY JSON. "
    "Judge whether the base action is appropriate for this turn."
)

class DSHTeacher:
    """LLM/DSH-backed teacher: generates scenarios and judges turns.

    It uses an injected client object with generate(LLMRequest), the same
    interface as OpenAICompatClient.  On any failure it falls back to a
    deterministic ScriptedTeacher, so offline runs never hard-fail.

    Cache policy: the system prompts are frozen module constants; scenario and
    judge payloads are appended per call.  DeepSeek prefix caching can then
    reuse the same system prefix instead of rebuilding it every turn.
    """

    def __init__(self, client=None, fallback: Optional[Teacher] = None,
                 n_scenarios: int = 50, seed: int = 1):
        self.client = client
        self.fallback = fallback or ScriptedTeacher(n_scenarios=n_scenarios, seed=seed)

    def _call(self, system: str, user: str) -> str:
        if self.client is None:
            raise RuntimeError("no DSH/LLM client configured")
        from .llm_adapter import build_prompt
        from .types import LLMRequest
        req = LLMRequest(mode="semantic", urgency=0.5, max_tokens=800,
                         reason="teacher", context={"system": system, "user": user})
        return str(self.client.generate(req))

    def scenario(self, seed: int) -> Scenario:
        # Stable prefix: DeepSeek context cache can reuse it across calls.
        system = _SCENARIO_SYSTEM
        user = (
            "Return a JSON object with keys: name (str), persona (str), goal (str), "
            "turns (array of 4-6 objects). Each turn object: text (str), directed (bool), "
            "expected (speak|silence|any), valence (number -1..1), topic (str). "
            "Make it diverse and emotionally plausible."
        )
        try:
            data = _extract_json(self._call(system, user))
            raw_turns = data.get("turns") or []
            turns: List[Turn] = []
            for x in raw_turns:
                if not isinstance(x, dict) or not x.get("text"):
                    continue
                turns.append(Turn(
                    text=str(x.get("text")),
                    directed=bool(x.get("directed", False)),
                    expected=str(x.get("expected", "any")),
                    valence=_clamp(x.get("valence", 0.0), -1.0, 1.0),
                    topic=str(x.get("topic", "chat")),
                ))
            if not turns:
                raise ValueError("empty scenario")
            return Scenario(name=str(data.get("name", "dsh_%d" % seed)),
                            persona=str(data.get("persona", "user")),
                            goal=str(data.get("goal", "chat")),
                            turns=turns)
        except Exception:
            return self.fallback.scenario(seed)

    def feedback(self, scenario: Scenario, turn: Turn, action: str, mind) -> float:
        try:
            state = {
                "v": float(getattr(mind.affect, "v", 0.0)),
                "stress": float(getattr(mind.affect, "stress", 0.0)),
                "energy": float(mind.drives.values.get("energy", 0.0)),
                "social": float(mind.drives.values.get("social", 0.0)),
                "viability": float(mind.viability_state.get("viability", 1.0)),
            }
        except Exception:
            state = {}
        # Stable prefix: DeepSeek context cache can reuse it across calls.
        system = _FEEDBACK_SYSTEM
        user = json.dumps({
            "scenario": scenario.name,
            "persona": scenario.persona,
            "turn": {"text": turn.text, "directed": turn.directed,
                     "expected": turn.expected, "valence": turn.valence},
            "action": action,
            "state": state,
            "output_schema": {"reward": "number -1..1", "reason": "short string"},
        }, ensure_ascii=False)
        try:
            data = _extract_json(self._call(system, user))
            return _clamp(data.get("reward", 0.0), -1.0, 1.0)
        except Exception:
            return self.fallback.feedback(scenario, turn, action, mind)
