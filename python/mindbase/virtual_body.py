"""A minimal virtual body: energy, hydration, warmth, pain, fatigue, sleep debt."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict

from .noise import clip


@dataclass
class VirtualBody:
    energy: float = 0.72
    hydration: float = 0.70
    warmth: float = 0.68
    pain: float = 0.00
    fatigue: float = 0.22
    sleep_debt: float = 0.10
    last_tick: float = field(default_factory=time.time)

    def tick(self, dt_seconds: float, awake: bool = True) -> None:
        dt = max(0.0, float(dt_seconds))
        hours = dt / 3600.0
        if awake:
            self.energy = clip(self.energy - 0.045 * hours - 0.015 * self.fatigue * hours, 0.0, 1.0)
            self.hydration = clip(self.hydration - 0.060 * hours, 0.0, 1.0)
            self.fatigue = clip(self.fatigue + 0.055 * hours, 0.0, 1.0)
            self.sleep_debt = clip(self.sleep_debt + 0.035 * hours, 0.0, 1.0)
        else:
            self.energy = clip(self.energy + 0.12 * hours, 0.0, 1.0)
            self.fatigue = clip(self.fatigue - 0.16 * hours, 0.0, 1.0)
            self.sleep_debt = clip(self.sleep_debt - 0.12 * hours, 0.0, 1.0)
            self.hydration = clip(self.hydration - 0.010 * hours, 0.0, 1.0)
        self.warmth = clip(self.warmth + (0.66 - self.warmth) * min(0.35, 0.04 * hours), 0.0, 1.0)
        self.pain = clip(self.pain - 0.08 * hours, 0.0, 1.0)
        self.last_tick = time.time()

    def act(self, action: str) -> Dict[str, float]:
        a = str(action or "").lower()
        if a == "eat":
            self.energy = clip(self.energy + 0.30, 0.0, 1.0)
            self.hydration = clip(self.hydration + 0.04, 0.0, 1.0)
        elif a == "drink":
            self.hydration = clip(self.hydration + 0.40, 0.0, 1.0)
        elif a in ("rest", "sleep"):
            self.energy = clip(self.energy + 0.10, 0.0, 1.0)
            self.fatigue = clip(self.fatigue - 0.30, 0.0, 1.0)
            self.sleep_debt = clip(self.sleep_debt - 0.25, 0.0, 1.0)
        elif a == "warm":
            self.warmth = clip(self.warmth + 0.30, 0.0, 1.0)
        elif a == "move":
            self.energy = clip(self.energy - 0.05, 0.0, 1.0)
            self.fatigue = clip(self.fatigue + 0.05, 0.0, 1.0)
        return self.summary()

    def apply_to(self, mind) -> None:
        try:
            mind.drives.values["energy"] = clip(self.energy, 0.0, 1.0)
            safety = clip(0.55 + 0.35 * self.warmth - 0.35 * self.pain, 0.0, 1.0)
            mind.drives.values["safety"] = safety
            load = 0.10 * self.fatigue + 0.20 * self.pain + 0.18 * max(0.0, 0.55 - self.warmth)
            mind.affect.stress = clip(mind.affect.stress + 0.12 * load, 0.0, 1.0)
            try:
                mind.physiology.hydration = clip(self.hydration, 0.0, 1.0)
                mind.physiology.pain = clip(self.pain, 0.0, 1.0)
                mind.physiology.sleep_pressure = clip(0.6 * self.fatigue + 0.4 * self.sleep_debt, 0.0, 1.0)
                mind.physiology.temperature = 37.0 - 2.5 * max(0.0, 0.5 - self.warmth)
            except Exception:
                pass
        except Exception:
            pass

    def metabolic_cost(self, arousal: float = 0.5, reply_chars: int = 0,
                       thinking: float = 1.0) -> None:
        """Chatting/thinking has a small real energy cost."""
        effort = 0.0008 * max(0.0, float(thinking))
        effort += 0.0009 * min(1.0, max(0, int(reply_chars)) / 200.0)
        effort += 0.0015 * max(0.0, min(1.0, float(arousal)))
        self.energy = clip(self.energy - effort, 0.0, 1.0)
        self.hydration = clip(self.hydration - effort * 0.35, 0.0, 1.0)
        self.fatigue = clip(self.fatigue + effort * 0.45, 0.0, 1.0)

    def summary(self) -> Dict[str, float]:
        return {"energy": round(self.energy, 3), "hydration": round(self.hydration, 3),
                "warmth": round(self.warmth, 3), "pain": round(self.pain, 3),
                "fatigue": round(self.fatigue, 3), "sleep_debt": round(self.sleep_debt, 3)}

    def text(self) -> str:
        return ("身体：能量 %.2f，水 %.2f，温度舒适 %.2f，疼痛 %.2f，疲劳 %.2f，睡眠债 %.2f"
                % (self.energy, self.hydration, self.warmth, self.pain, self.fatigue, self.sleep_debt))
