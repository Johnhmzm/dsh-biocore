from __future__ import annotations

import random
from collections import Counter
from typing import Dict, Tuple

from .types import Event


class GridWorld:
    """Minimal closed loop: action changes position, position changes observation."""

    def __init__(self, seed: int = 1, size: int = 7, noise: float = 0.0):
        self.rng = random.Random(seed)
        self.size = size
        self.noise = float(noise)
        self.pos = [size // 2, size // 2]
        self.energy = 1.0
        self.food = self._place()
        self.threat = self._place()

    def _place(self) -> Tuple[int, int]:
        return (self.rng.randrange(self.size), self.rng.randrange(self.size))

    def _dir(self, target) -> str:
        dx, dy = target[0] - self.pos[0], target[1] - self.pos[1]
        ns = "北" if dy < 0 else "南" if dy > 0 else ""
        ew = "西" if dx < 0 else "东" if dx > 0 else ""
        return ns + ew or "这里"

    def observe(self) -> str:
        if self.rng.random() < self.noise:
            return "感知中断"
        return f"食物在{self._dir(self.food)}，危险在{self._dir(self.threat)}，能量{self.energy:.2f}"

    def _move(self, dx: int, dy: int) -> None:
        self.pos[0] = max(0, min(self.size - 1, self.pos[0] + dx))
        self.pos[1] = max(0, min(self.size - 1, self.pos[1] + dy))

    def potential(self) -> float:
        dist = abs(self.pos[0] - self.food[0]) + abs(self.pos[1] - self.food[1])
        return round(1.0 - dist / max(1.0, 2.0 * (self.size - 1)), 4)

    def shock(self) -> None:
        # environment change: food and threat relocate, small energy hit
        self.food = self._place()
        self.threat = self._place()
        self.energy = max(0.0, self.energy - 0.10)

    def act(self, kind: str) -> float:
        if kind == "approach":
            dx = 1 if self.food[0] > self.pos[0] else -1 if self.food[0] < self.pos[0] else 0
            dy = 1 if self.food[1] > self.pos[1] else -1 if self.food[1] < self.pos[1] else 0
            self._move(dx, dy)
        elif kind == "avoid":
            dx = -1 if self.threat[0] > self.pos[0] else 1 if self.threat[0] < self.pos[0] else 0
            dy = -1 if self.threat[1] > self.pos[1] else 1 if self.threat[1] < self.pos[1] else 0
            self._move(dx, dy)
        elif kind in ("explore", "communicate"):
            if kind == "communicate" and self.energy < 0.40:
                self.act("approach")
            else:
                self._move(self.rng.choice((-1, 0, 1)), self.rng.choice((-1, 0, 1)))
        elif kind == "rest":
            self.energy = min(1.0, self.energy + 0.05)
        self.energy -= 0.01
        reward = 0.0
        if tuple(self.pos) == self.food:
            self.energy = min(1.0, self.energy + 0.20)
            self.food = self._place()
            reward = 0.2
        if tuple(self.pos) == self.threat:
            self.energy = max(0.0, self.energy - 0.15)
            self.threat = self._place()
            reward = -0.2
        return reward


class EmbodiedLoop:
    def __init__(self, mind, env: GridWorld | None = None, traits: Dict[str, float] | None = None):
        self.mind = mind
        self.env = env or GridWorld(seed=1)
        self.traits = dict(traits or {})

    def run(self, ticks: int = 200, shock_every: int = 0) -> Dict[str, object]:
        kinds, actions = Counter(), Counter()
        for i in range(ticks):
            if shock_every and i > 0 and i % shock_every == 0:
                self.env.shock()
            obs = self.env.observe()
            out = self.mind.step(Event(text=obs, speaker="world", directed=False, ts=self.mind.t + 60))
            if out.action == "silence":
                hunger = float(self.traits.get("hunger_threshold", 0.50))
                if self.env.energy < hunger:
                    kind = "approach"
                elif self.env.energy < 0.25:
                    kind = "rest"
                else:
                    kind = "explore"
            else:
                kind = out.act.kind if out.act else "communicate"
            reward = self.env.act(kind)
            self.mind.drives.values["energy"] = self.env.energy
            if reward:
                self.mind.post_reply("", feedback=reward, speaker="world")
            actions[out.action] += 1
            kinds[kind] += 1
        self_initiated = sum(v for k, v in kinds.items() if k != "communicate")
        return {
            "ticks": ticks,
            "actions": dict(actions),
            "kinds": dict(kinds),
            "energy": round(self.env.energy, 3),
            "pos": list(self.env.pos),
            "viability": self.mind.viability_state.get("viability", 1.0),
            "scar": round(self.mind.circuit.chronic_scar, 3),
            "critical_flexibility": round(float(getattr(getattr(self.mind, "critical", None), "metastability", 0.0)), 4),
            "autonomy": {
                "survival": round(self.env.energy, 3),
                "initiative": round(self_initiated / max(1, ticks), 3),
                "viability": round(self.mind.viability_state.get("viability", 1.0), 3),
            },
        }
