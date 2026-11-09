"""Energy-action policy: forage / rest / socialize.

The policy weights are genes.  The base's tick decision can use it when
heads["energy_policy"] is attached; default base behavior is unchanged.
"""
from __future__ import annotations

import math
import random

from .quantum import QuantumUncertainty
from .symbolic import eval_expr
from typing import Dict, Tuple


class ForagePolicy:
    def __init__(self, forage_weight: float = 1.0, rest_weight: float = 0.7,
                 social_weight: float = 0.5, temperature: float = 0.3,
                 fear_weight: float = 0.5, curiosity_weight: float = 0.3,
                 explore_weight: float = 0.5, drink_weight: float = 0.6,
                 heal_weight: float = 0.5, need_physio_gate: float = 0.8,
                 need_safety_gate: float = 0.8, need_belong_gate: float = 0.8,
                 need_actual_gate: float = 0.8, planning: float = 0.5,
                 quantum_weight: float = 0.5, quantum_gamma: float = 0.05,
                 quantum_dephase: float = 0.10, formula_expr=None, world_model=None,
                 world_weight: float = 0.5, world_horizon: int = 1, world_beam: int = 1,
                 value_model=None, value_weight: float = 0.5,
                 world_discount: float = 0.9, emotion_social_weight: float = 0.5,
                 emotion_avoid_weight: float = 0.5, emotion_explore_weight: float = 0.5,
                 emotion_recovery_weight: float = 0.5,
                 rng: random.Random | None = None):
        self.w_forage = float(forage_weight)
        self.w_rest = float(rest_weight)
        self.w_social = float(social_weight)
        self.w_fear = float(fear_weight)
        self.w_curiosity = float(curiosity_weight)
        self.w_explore = float(explore_weight)
        self.w_drink = float(drink_weight)
        self.w_heal = float(heal_weight)
        self.g_phys = float(need_physio_gate)
        self.g_safe = float(need_safety_gate)
        self.g_belong = float(need_belong_gate)
        self.g_actual = float(need_actual_gate)
        self.planning = max(0.0, min(1.0, float(planning)))
        self.quantum_weight = max(0.0, float(quantum_weight))
        self.quantum_gamma = max(0.0, float(quantum_gamma))
        self.quantum_dephase = max(0.0, float(quantum_dephase))
        self._quantum = None
        self.formula_expr = formula_expr
        self.world_model = world_model
        self.world_weight = float(world_weight)
        self.world_horizon = max(1, int(world_horizon))
        self.world_beam = max(1, int(world_beam))
        self.value_model = value_model
        self.value_weight = float(value_weight)
        self.world_discount = float(world_discount)
        self.w_emo_social = float(emotion_social_weight)
        self.w_emo_avoid = float(emotion_avoid_weight)
        self.w_emo_explore = float(emotion_explore_weight)
        self.w_emo_recovery = float(emotion_recovery_weight)
        self.temperature = max(0.02, float(temperature))
        self.rng = rng or random.Random(1)

    def choose(self, state: Dict[str, float]) -> Tuple[str, float]:
        energy = max(0.0, min(1.0, float(state.get("energy", 0.5))))
        social = max(0.0, min(1.0, float(state.get("social", 0.5))))
        stress = max(0.0, min(1.0, float(state.get("stress", 0.0))))
        fear = max(0.0, min(1.0, float(state.get("fear", 0.0))))
        curiosity = max(0.0, min(1.0, float(state.get("curiosity", 0.5))))
        known_food = max(0.0, min(1.0, float(state.get("known_food", 0.0))))
        uncertainty = max(0.0, min(1.0, float(state.get("food_uncertainty", 1.0 - known_food))))
        hunger = max(0.0, min(1.0, float(state.get("hunger", 1.0 - energy))))
        thirst = max(0.0, min(1.0, float(state.get("thirst", 0.2))))
        pain_i = max(0.0, min(1.0, float(state.get("pain", 0.0))))
        inflam = max(0.0, min(1.0, float(state.get("inflammation", 0.0))))
        sleepp = max(0.0, min(1.0, float(state.get("sleep_pressure", 0.2))))
        lonely = max(0.0, min(1.0, float(state.get("loneliness", 1.0 - social))))
        need_phys = max(0.0, min(1.0, float(state.get("need_physiological", 0.0))))
        need_safe = max(0.0, min(1.0, float(state.get("need_safety", 0.0))))
        need_belong = max(0.0, min(1.0, float(state.get("need_belonging", 0.0))))
        need_actual = max(0.0, min(1.0, float(state.get("need_actualization", 0.0))))
        joy_e = max(0.0, min(1.0, float(state.get("joy", 0.0))))
        love_e = max(0.0, min(1.0, float(state.get("love", 0.0))))
        lonely_e = max(0.0, min(1.0, float(state.get("loneliness", 0.0))))
        fear_e = max(0.0, min(1.0, float(state.get("fear", 0.0))))
        anger_e = max(0.0, min(1.0, float(state.get("anger", 0.0))))
        sadness_e = max(0.0, min(1.0, float(state.get("sadness", 0.0))))
        awe_e = max(0.0, min(1.0, float(state.get("awe", 0.0))))
        social_emotion = 0.5 * joy_e + 0.5 * love_e + 0.3 * lonely_e
        avoid_emotion = 0.5 * fear_e + 0.5 * anger_e
        explore_emotion = 0.4 * joy_e + 0.3 * awe_e + 0.3 * (1.0 - max(0.0, min(1.0, float(state.get("stress", 0.0)))))
        recovery_emotion = sadness_e + 0.5 * fear_e
        scores = [
            (self.w_forage * hunger + self.w_curiosity * known_food) * (1.0 + self.g_phys * need_phys),
            (self.w_explore * hunger + self.w_curiosity * uncertainty) * (1.0 + self.g_actual * need_actual),
            self.w_rest * (sleepp + self.w_fear * fear + max(0.0, 0.4 - energy)) * (1.0 + self.g_safe * need_safe),
            self.w_drink * thirst * (1.0 + self.g_phys * need_phys),
            (self.w_heal * inflam + self.w_heal * 0.5 * pain_i) * (1.0 + self.g_phys * need_phys),
            self.w_social * lonely * (1.0 + self.g_belong * need_belong)
            + self.w_emo_social * social_emotion,
        ]
        scores[1] += self.w_emo_explore * explore_emotion
        scores[2] += self.w_emo_avoid * avoid_emotion + self.w_emo_recovery * recovery_emotion
        if self.world_model is not None and self.planning > 0.0:
            try:
                base_vec = [energy, social, stress, pain_i,
                            max(0.0, min(1.0, float(state.get("viability", 1.0)))),
                            max(0.0, min(1.0, float(state.get("needs_satisfaction", 0.5))))]
                h0 = getattr(self.world_model, "h", None)
                h0 = h0.copy() if h0 is not None else None
                # beam search over action sequences
                branches = [(h0.copy() if h0 is not None else None, list(base_vec), 0.0, None)]
                for _depth in range(max(1, self.world_horizon)):
                    nxt = []
                    for _bh, _vec, _total, _first in branches:
                        for _i in range(6):
                            if _bh is not None:
                                self.world_model.h = _bh.copy()
                            pred = self.world_model.predict_next(_vec, _i)
                            if self.value_model is not None:
                                value = self.value_weight * float(self.value_model.predict(pred[:6], _i))
                            else:
                                value = (0.3 * float(pred[5]) + 0.2 * float(pred[0])
                                         + 0.1 * float(pred[1]) - 0.2 * float(pred[2]))
                            _nvec = [float(x) for x in pred[:6]]
                            _nt = _total + (self.world_discount ** _depth) * value
                            _nf = _first if _first is not None else _i
                            _nh = self.world_model.h.copy() if hasattr(self.world_model, "h") else None
                            nxt.append((_nh, _nvec, _nt, _nf))
                    branches = sorted(nxt, key=lambda x: x[2], reverse=True)[:max(1, self.world_beam)]
                if branches:
                    _bh, _vec, _total, _first = max(branches, key=lambda x: x[2])
                    if _first is not None:
                        scores[int(_first)] += self.planning * self.world_weight * _total / max(1, self.world_horizon)
                if h0 is not None:
                    self.world_model.h = h0.copy()
            except Exception:
                pass
        if self.planning > 0.0:
            # one-step model-based lookahead: predict how each action changes
            # the need deficits, then add planning * future-value to the score
            eff_phys = (0.16, 0.10, 0.12, 0.16, 0.18, 0.00)
            eff_safe = (0.00, 0.00, 0.10, 0.00, 0.06, 0.00)
            eff_belong = (0.00, 0.00, 0.00, 0.00, 0.00, 0.25)
            eff_actual = (0.05, 0.20, 0.00, 0.00, 0.00, 0.00)
            np = max(0.0, min(1.0, float(state.get("need_physiological", 0.0))))
            ns = max(0.0, min(1.0, float(state.get("need_safety", 0.0))))
            nb = max(0.0, min(1.0, float(state.get("need_belonging", 0.0))))
            na = max(0.0, min(1.0, float(state.get("need_actualization", 0.0))))
            for i in range(len(scores)):
                fp = max(0.0, np - eff_phys[i])
                fs = max(0.0, ns - eff_safe[i])
                fb = max(0.0, nb - eff_belong[i])
                fa = max(0.0, na - eff_actual[i])
                value = -(fp + 0.8 * fs + 0.6 * fb + 0.4 * fa)
                scores[i] += self.planning * 2.0 * value
        if self.formula_expr is not None:
            try:
                bonus = float(eval_expr(self.formula_expr, dict(state)))
                scores[0] += max(-0.5, min(0.5, 0.1 * bonus))
            except Exception:
                pass
        temp_eff = self.temperature
        if self.quantum_weight > 0.0:
            if self._quantum is None:
                self._quantum = QuantumUncertainty.from_affect(0.0, max(0.0, 1.0 - energy), stress)
            self._quantum.step(1.0, gamma=self.quantum_gamma, dephase=self.quantum_dephase)
            temp_eff = max(0.02, self.temperature * (1.0 + self.quantum_weight * self._quantum.entropy()))
        m = max(scores)
        ex = [math.exp((s - m) / temp_eff) for s in scores]
        total = sum(ex)
        probs = [x / total for x in ex]
        r = self.rng.random()
        acc = 0.0
        for action, p in zip(("forage", "explore", "rest", "drink", "heal", "socialize"), probs):
            acc += p
            if r <= acc:
                return action, round(p, 4)
        return "rest", round(probs[2], 4)
