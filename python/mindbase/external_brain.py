"""External brain proposal + math verification loop.

The external brain proposes; the math verifier scores; the base accepts or
rejects.  No proposal has direct action authority.

This prototype uses a deterministic stub brain.  A real LLM/DSH client can
implement the same interface later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Sequence

from .autonomy_evolve import AutonomyGenome, evaluate
from .types import LLMRequest


@dataclass
class Proposal:
    kind: str
    payload: object
    source: str = "external"


class StubExternalBrain:
    """Offline stand-in for an LLM: proposes a few candidate formulas/plans."""

    def propose_formulas(self, state: Dict[str, object] | None = None) -> List[Proposal]:
        formulas = [
            ("+", "energy", "hunger"),
            ("-", 1, "thirst"),
            ("*", "fear", "food_uncertainty"),
            ("+", "known_food", "need_actualization"),
        ]
        return [Proposal("formula", f, "stub") for f in formulas]

    def propose_plans(self, state: Dict[str, object] | None = None) -> List[Proposal]:
        plans = [
            ("forage", "drink", "rest"),
            ("drink", "forage", "heal"),
            ("rest", "forage", "drink"),
        ]
        return [Proposal("plan", p, "stub") for p in plans]


class MathVerifier:
    def __init__(self, steps: int = 80, seeds: Sequence[int] = (1, 2), env: Dict[str, object] | None = None):
        self.steps = int(steps)
        self.seeds = tuple(seeds)
        self.env = dict(env or {})

    def score_formula(self, base: AutonomyGenome, formula) -> Dict[str, object]:
        g = AutonomyGenome(**base.__dict__)
        g.formula_expr = formula
        rows = [evaluate(g, steps=self.steps, seed=s, **self.env) for s in self.seeds]
        return {"mean_fitness": sum(float(r["fitness"]) for r in rows) / len(rows),
                "min_lifespan": min(int(r["lifespan"]) for r in rows),
                "mean_needs": sum(float(r.get("needs_satisfaction") or 0.0) for r in rows) / len(rows),
                "mean_pending_damage": sum(float(r.get("pending_damage_sum", 0.0)) for r in rows) / len(rows)}

    def verify_formulas(self, base: AutonomyGenome, proposals: Sequence[Proposal]) -> Dict[str, object]:
        base_rows = [evaluate(base, steps=self.steps, seed=s, **self.env) for s in self.seeds]
        base_score = sum(float(r["fitness"]) for r in base_rows) / len(base_rows)
        scored = []
        for p in proposals:
            if p.kind != "formula":
                continue
            s = self.score_formula(base, p.payload)
            s["formula"] = p.payload
            s["source"] = p.source
            s["accepted"] = bool(s["mean_fitness"] > base_score)
            scored.append(s)
        scored.sort(key=lambda x: x["mean_fitness"], reverse=True)
        return {"base_score": round(base_score, 5), "proposals": scored,
                "accepted": [x for x in scored if x["accepted"]],
                "best": scored[0] if scored else None}


def run_external_loop(base: AutonomyGenome, steps: int = 80,
                      seeds: Sequence[int] = (1, 2, 3),
                      env: Dict[str, object] | None = None,
                      brain=None) -> Dict[str, object]:
    brain = brain if brain is not None else StubExternalBrain()
    verifier = MathVerifier(steps=steps, seeds=seeds, env=env)
    proposals = brain.propose_formulas()
    result = verifier.verify_formulas(base, proposals)
    return {"proposals": [p.payload for p in proposals], "verification": result,
            "accepted_count": len(result["accepted"])}



class LLMExternalBrain:
    """Real LLM adapter: proposes structured formulas, falls back to stub."""

    PROMPT = (
        "You are an external proposal brain for a low-level survival agent. "
        "Return ONLY a JSON list of expression trees. Each tree uses + - * / "
        "and variables: energy, social, stress, hunger, thirst, pain, "
        "inflammation, sleep_pressure, known_food, food_uncertainty. "
        "Example: [[\"+\",\"energy\",\"hunger\"]]"
    )

    def __init__(self, client=None, fallback=None):
        self.client = client
        self.fallback = fallback or StubExternalBrain()

    @classmethod
    def from_env(cls):
        try:
            from .llm_openai import OpenAICompatClient
            return cls(OpenAICompatClient.from_env())
        except Exception:
            return cls(None)

    def propose_formulas(self, state=None) -> List[Proposal]:
        if self.client is None:
            return self.fallback.propose_formulas(state)
        req = LLMRequest(mode="tool", urgency=0.3, max_tokens=400,
                         reason="external_brain_formula_proposal",
                         context={"instruction": self.PROMPT, "state": dict(state or {})})
        try:
            text = str(self.client.generate(req)).strip()
            start = text.find("[")
            end = text.rfind("]")
            if start < 0 or end <= start:
                return self.fallback.propose_formulas(state)
            rows = json.loads(text[start:end + 1])
            out = []
            for row in rows:
                if isinstance(row, list) and row:
                    out.append(Proposal("formula", tuple(row), "llm"))
            return out or self.fallback.propose_formulas(state)
        except Exception:
            return self.fallback.propose_formulas(state)



def freeze_accepted(base: AutonomyGenome, result: Dict[str, object],
                    path: str | None = None) -> AutonomyGenome:
    """Distill an accepted proposal into the base; no LLM at runtime."""
    best = (result.get("verification") or {}).get("best") if isinstance(result, dict) else None
    if not best or not best.get("accepted"):
        return AutonomyGenome(**base.__dict__)
    g = AutonomyGenome(**base.__dict__)
    g.formula_expr = best.get("formula")
    if path:
        try:
            import json
            from dataclasses import asdict
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"genome": asdict(g), "source": best.get("source"),
                           "fitness": best.get("mean_fitness")}, f,
                          ensure_ascii=False, indent=1, default=str)
        except Exception:
            pass
    return g
