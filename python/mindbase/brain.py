"""Functional brain modules: amygdala, hippocampus, PFC, thalamus, insula,
striatum, dopamine and a small default-mode/self-referential loop.

Not a neural simulation. Numeric modules with explicit loops and behavior hooks.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _clip(x, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


@dataclass
class Amygdala:
    threat: float = 0.0
    sensitization: float = 0.0

    def stimulate(self, valence: float, arousal: float, social_negative: bool,
                  repetition: float = 0.0) -> float:
        neg = max(0.0, -float(valence))
        raw = 0.45 * neg + 0.25 * _clip(arousal) + (0.20 if social_negative else 0.0)
        raw += 0.10 * _clip(repetition) + 0.15 * self.sensitization
        self.threat = _clip(0.55 * self.threat + 0.65 * raw)
        self.sensitization = _clip(self.sensitization + 0.02 * self.threat - 0.005)
        return self.threat

    def decay(self, dt_min: float = 1.0) -> None:
        dt = max(0.0, float(dt_min))
        self.threat = _clip(self.threat * (1.0 - min(0.5, 0.01 * dt)))
        self.sensitization = _clip(self.sensitization - 0.001 * dt)


@dataclass
class Hippocampus:
    safety: float = 0.5
    last_context: float = 0.0

    def bind(self, valence: float, threat: float, trust: float = 0.5) -> float:
        ctx = 0.5 + 0.5 * max(-1.0, min(1.0, float(valence))) * _clip(trust)
        self.last_context = ctx
        self.safety = _clip(0.85 * self.safety + 0.15 * (0.6 * ctx + 0.4 * (1.0 - threat)))
        return self.safety


@dataclass
class Prefrontal:
    inhibition: float = 0.5
    working_memory: list = field(default_factory=list)

    def regulate(self, threat: float, safety: float, load: float = 0.0) -> float:
        self.inhibition = _clip(0.35 + 0.35 * _clip(safety) + 0.20 * (1.0 - _clip(load)))
        regulated = _clip(threat * (1.0 - 0.65 * self.inhibition))
        self.working_memory = (self.working_memory + [round(threat, 3)])[-6:]
        return regulated


@dataclass
class Thalamus:
    focus: float = 0.5

    def relay(self, valence: float, arousal: float, social_negative: bool,
              stress: float = 0.0) -> float:
        salience = 0.25 + 0.35 * abs(float(valence)) + 0.20 * _clip(arousal)
        salience += 0.15 if social_negative else 0.0
        salience *= (1.0 - 0.25 * _clip(stress))
        self.focus = _clip(salience)
        return self.focus


@dataclass
class Insula:
    intero_load: float = 0.2
    sensation: float = 0.5

    def read(self, body: dict | None, stress: float = 0.0) -> float:
        b = body or {}
        tired = float(b.get("fatigue", 0.0) or 0.0)
        hypo = 1.0 - float(b.get("hydration", 0.6) or 0.6)
        cold = abs(float(b.get("warmth", 0.68) or 0.68) - 0.68)
        pain = float(b.get("pain", 0.0) or 0.0)
        self.intero_load = _clip(0.20 * tired + 0.20 * hypo + 0.15 * cold +
                                 0.25 * pain + 0.20 * _clip(stress))
        self.sensation = _clip(0.5 + 0.5 * (self.intero_load - 0.2))
        return self.intero_load


@dataclass
class Striatum:
    values: dict = field(default_factory=lambda: {
        "speak": 0.55, "withdraw": 0.30, "self_care": 0.45, "wait": 0.30})
    dopamine: float = 0.5
    last_action: str = "speak"

    def choose(self, threat: float, safety: float, energy: float,
               intero_load: float = 0.0) -> str:
        utils = {
            "speak": 0.55 + 0.20 * (1.0 - threat) + 0.15 * safety,
            "withdraw": 0.25 + 0.45 * threat + 0.10 * (1.0 - safety),
            "self_care": 0.25 + 0.50 * intero_load + 0.15 * (1.0 - energy),
            "wait": 0.25 + 0.20 * threat + 0.15 * (1.0 - energy),
        }
        action = max(utils, key=lambda k: utils[k] + 0.25 * self.values.get(k, 0.0))
        self.last_action = action
        return action

    def reward(self, reward: float) -> float:
        baseline = 0.5
        rpe = _clip(float(reward) - baseline, -1.0, 1.0)
        self.dopamine = _clip(self.dopamine + 0.15 * rpe)
        self.values[self.last_action] = _clip(self.values.get(self.last_action, 0.5) + 0.10 * rpe)
        return round(self.dopamine, 4)


@dataclass
class DefaultMode:
    self_focus: float = 0.3
    narrative: float = 0.3

    def tick(self, connection: float = 0.0, stress: float = 0.0,
             idle_min: float = 1.0) -> float:
        idle = _clip(max(0.0, float(idle_min)) / 30.0)
        self.self_focus = _clip(0.25 + 0.35 * _clip(connection) +
                                0.20 * _clip(stress) + 0.20 * idle)
        self.narrative = _clip(0.5 * self.self_focus + 0.5 * (1.0 - _clip(connection)))
        return self.self_focus


@dataclass
class BrainLoop:
    amygdala: Amygdala = field(default_factory=Amygdala)
    hippocampus: Hippocampus = field(default_factory=Hippocampus)
    pfc: Prefrontal = field(default_factory=Prefrontal)
    thalamus: Thalamus = field(default_factory=Thalamus)
    insula: Insula = field(default_factory=Insula)
    striatum: Striatum = field(default_factory=Striatum)
    dmn: DefaultMode = field(default_factory=DefaultMode)
    last: dict = field(default_factory=dict)

    def tick(self, valence: float, arousal: float, social_negative: bool = False,
             repetition: float = 0.0, trust: float = 0.5,
             load: float = 0.0, dt_min: float = 1.0,
             body: dict | None = None, energy: float = 0.7,
             connection: float = 0.0) -> dict:
        self.amygdala.decay(dt_min)
        focus = self.thalamus.relay(valence, arousal, social_negative, load)
        raw = self.amygdala.stimulate(valence, arousal, social_negative, repetition)
        safety = self.hippocampus.bind(valence, raw, trust)
        intero = self.insula.read(body, load)
        regulated = self.pfc.regulate(raw, safety, max(load, intero))
        action = self.striatum.choose(regulated, safety, energy, intero)
        self.dmn.tick(connection, load, dt_min)
        self.last = {
            "threat_raw": round(raw, 4), "threat": round(regulated, 4),
            "safety": round(safety, 4), "inhibition": round(self.pfc.inhibition, 4),
            "focus": round(focus, 4), "intero_load": round(intero, 4),
            "action": action, "dopamine": round(self.striatum.dopamine, 4),
            "self_focus": round(self.dmn.self_focus, 4),
        }
        return self.last

    def reward(self, reward: float) -> float:
        return self.striatum.reward(reward)

    def context(self) -> str:
        if not self.last:
            return ""
        return ("杏仁核威胁=%.2f 海马安全=%.2f 前额叶抑制=%.2f 丘脑显著性=%.2f "
                "岛叶内感=%.2f 纹状体动作=%s 多巴胺=%.2f 自我聚焦=%.2f；"
                "威胁要经过安全记忆和抑制，不要直接反应。"
                % (self.last.get("threat_raw", 0.0), self.last.get("safety", 0.5),
                   self.last.get("inhibition", 0.5), self.last.get("focus", 0.5),
                   self.last.get("intero_load", 0.0), self.last.get("action", "speak"),
                   self.last.get("dopamine", 0.5), self.last.get("self_focus", 0.0)))
