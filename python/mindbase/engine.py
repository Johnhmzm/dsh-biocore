from __future__ import annotations

import random
from typing import Any, Dict, Iterable, List, Optional

from .affect import Affect
from .actions import ACTION_KINDS, Action
from .appraisal import Appraiser
from .attention import Attention
from .active_inference import ActiveInference
from .theory import unified_state
from .attachment import Attachment
from .circuit import Circuit
from .critical import CriticalDynamics
from .viability import Viability
from .autonomy import Autonomy
from .clock import PopulationClock
from .config import MindConfig
from .drives import Drives
from .goals import GoalStack
from .expression import policy_for
from .intention import IntentionStack
from .invariants import assert_ok, health as invariants_health
from .learner import Learner
from .lifecycle import Lifecycle
from .llm_gate import LLMGate
from .mb import MushroomBody
from .layered_memory import LayeredMemory
from .meta import Meta
from .narrative import NarrativeIdentity
from .needs import NeedPyramid
from .persona import Persona
from .physiology import HumanPhysiology
from .self_model import SelfModel
from .theory_of_mind import TheoryOfMind
from .workspace import Candidate, GlobalWorkspace
from .social import Social
from .noise import clip
from .types import Appraisal, Event, Feedback, Outcome
from .uncertainty_field import UncertaintyField
from .values import ValueSystem
from .will import Volition
from .working_memory import WorkingMemory


class MindBase:
    """The base: event/tick -> state -> action decision.

    It does not call an LLM. It exposes prompt_state() for the expression layer.
    """

    def __init__(self, cfg: Optional[MindConfig] = None, seed: Optional[int] = None, heads: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or MindConfig()
        if seed is not None:
            self.cfg.seed = seed
        self.rng = random.Random(self.cfg.seed)
        self.t = 0.0
        self.heads: Dict[str, Any] = dict(heads or {})
        self.last_appraisal = None
        self.last_action_advice = None

        self.drives = Drives(self.cfg, self.rng)
        self.clock = PopulationClock(self.cfg, self.rng)
        self.appraiser = Appraiser(self.cfg, self.rng)
        self.affect = Affect(self.cfg, self.rng)
        self.physiology = HumanPhysiology(self.cfg, self.rng)
        self.needs = NeedPyramid(self.cfg)
        self.uncertainty = UncertaintyField(self.cfg, self.rng)
        self.attention = Attention()
        self.wm = WorkingMemory(self.cfg)
        self.memory = LayeredMemory(self.cfg, self.rng)
        self.social = Social(self.cfg, self.rng)
        if self.cfg.persona_profile:
            self.persona = Persona.from_profile(self.cfg.persona_profile, seed=self.cfg.seed)
        else:
            self.persona = Persona.from_seed(self.cfg.seed)
        self.values = ValueSystem.from_persona(self.persona)
        self.goals = GoalStack(self.cfg)
        self.self_model = SelfModel(self.rng)
        self.tom = TheoryOfMind()
        self.workspace = GlobalWorkspace()
        self.meta = Meta(self.cfg)
        self.autonomy = Autonomy(self.cfg, self.rng)
        self.attachment = Attachment()
        self.circuit = Circuit()
        self.critical = CriticalDynamics(self.cfg.seed)
        self.viability = Viability()
        self.viability_state = {"viability": 1.0, "deficits": {}, "alarm": 0.0}
        self.active_inference = ActiveInference()
        self.intentions = IntentionStack(self.cfg)
        self.will = Volition(self.cfg, self.rng)
        self.mb = MushroomBody()
        self.learner = Learner(self.cfg, self.mb, self.rng)
        self.llm_gate = LLMGate(self.cfg)
        self.lifecycle = Lifecycle(self.cfg, self.rng)

        self.history: List[Outcome] = []
        self.last_outcome: Optional[Outcome] = None
        self.last_speaker: Optional[str] = None
        self.last_user_text: Optional[str] = None
        self.narrative = NarrativeIdentity()
        if self.cfg.persona_profile:
            self.persona.apply_to(self)

    def step(self, ev: Event) -> Outcome:
        dt = self.cfg.dt if ev.ts is None else max(0.0, ev.ts - self.t)
        if dt <= 0:
            dt = self.cfg.dt
        self.t += dt

        self.clock.tick(dt, activity=1.0 if ev.kind == "message" else 0.0, asleep=self.clock.asleep)
        self.drives.tick(dt, asleep=self.clock.asleep)

        mem_pred = self.memory.predict(ev.text, self.t) if ev.text else None
        ap = self.appraiser.appraise(ev, state=self, social=self.social)
        # predictive self: previous self-prediction error modulates appraisal
        ap.rpe += 0.15 * self.self_model.prediction_error
        # memory prediction error: expected text vs incoming text
        if mem_pred is not None:
            same = (ev.text in mem_pred.text) or (mem_pred.text in ev.text)
            self.memory_prediction_error = 0.0 if same else 0.25
            ap.novelty = clip(ap.novelty + 0.15 * self.memory_prediction_error, 0.0, 1.0)
        else:
            self.memory_prediction_error = 0.0
        if self.heads.get("appraisal") is not None:
            ap = self._blend_appraisal(ev, ap)
        self.last_appraisal = ap
        self.affect.update(ap, self.drives, dt)
        self.physiology.update(dt, energy=self.drives.get("energy"), stress=self.affect.stress,
                               social=self.drives.get("social"), asleep=self.clock.asleep,
                               load=abs(float(ap.valence)) + float(ap.novelty))
        self.needs.update(self.drives.get("energy"), self.physiology.hydration,
                          self.physiology.sleep_pressure, self.physiology.pain,
                          self.physiology.inflammation, float(self.viability_state.get("viability", 1.0)),
                          self.affect.stress, self.drives.get("social"),
                          float(getattr(self.self_model, "identity", 0.5)), float(self.meta.confidence),
                          self.drives.get("curiosity"), float(ap.novelty),
                          mode=("chat" if ev.kind == "message" else "survival"))
        self.uncertainty.update(dt, load=abs(float(ap.valence)) + float(ap.novelty))
        if self.uncertainty.gains.get("affect", 0.0) > 0.0:
            self.affect.stress = self.uncertainty.apply(self.affect.stress, "affect", scale=0.02, lo=0.0, hi=1.0)
        self.memory.set_affect(self.affect.v, self.affect.a)
        self.social.on_event(ev, ap, self.t, dt)
        self.tom.observe(ev, ap, self.t)
        self.social.tick(dt, self.t)
        if ev.kind == "message" and ev.speaker and ev.speaker != "self":
            self.last_speaker = ev.speaker
        if ev.kind == "message" and ev.text:
            self.last_user_text = ev.text
        self.learner.on_event(ev.text, ap.valence, self.t)

        salience = self.attention.update(ev, ap, self.drives)
        salience = clip(salience + 0.15 * float(getattr(self, "memory_prediction_error", 0.0)), 0.0, 1.0)
        self.wm.tick(dt, self.t)
        self.wm.push(ev.text, salience, self.t)
        self.circuit.update(salience, reward=max(0.0, ap.valence), punishment=max(0.0, -ap.valence))
        self.critical_state = self.critical.step(salience)
        self.circuit.enforce_capacity()
        self.circuit_valence = self.circuit.valence()
        self.drives.metabolic_cost(self.circuit.metabolic_cost())

        hit = self.memory.retrieve(ev.text, self.t)
        importance = max(ap.relevance, 0.8 * ap.novelty, abs(ap.valence), 0.6 * salience)
        if ev.kind == "message" and ev.speaker and ev.speaker != "self":
            importance = min(1.0, importance * self.attachment.memory_bias())
        importance = min(1.0, importance * self.circuit.write_gain())
        self.memory.write(ev.text, importance, ap.rpe, self.t, valence=ap.valence, arousal=ap.arousal)
        self.workspace.compete(self._workspace_candidates(ev, ap, salience, hit), self.t)
        self.last_action_advice = self._action_advice(ev)
        self.last_event_kind = ev.kind

        self.self_model.on_event(ev, ap)
        self.self_model.tick(dt)
        self.meta.update(ap, memory_hit=bool(hit), stress=self.affect.stress)
        self.autonomy.tick(dt, self.clock, self.drives, self.affect.mood, self.drives.allostatic_load(), self.affect.fatigue, self.attachment.initiation_bias())
        lc = self.drives.load_components()
        support = self.social.trust_for(self.last_speaker) if self.last_speaker else 0.0
        self.self_model.update_allostatic_efficacy(self.drives.allostatic_load(), self.meta.confidence, self.attachment.anxiety, lc["social"], lc["physiological"], lc["safety"], support)
        self.viability_state = self.viability.update(self.drives)
        self.viability.alarm = self.viability_state["alarm"]
        if self.viability.alarm > 0:
            self.affect.stress = clip(self.affect.stress + 0.10 * self.viability.alarm, 0.0, 1.0)
        else:
            self.affect.stress = clip(self.affect.stress - 0.002 * (dt / 3600.0), 0.0, 1.0)
        self.affect.update_self_efficacy(self.self_model.allostatic_self_efficacy, dt)
        util = self.circuit.capacity_report()["utilization"]
        self.affect.update_structural_load(util, dt)
        self.lifecycle.add_structural_load(util, dt)
        if self.lifecycle.sleep_debt > 1.20:
            self.circuit.chronic_stress(0.01)
        self.intentions.tick(dt, self.t)
        self.goals.tick(dt, self.t)
        self.goals.sync_intentions(self.intentions, self.values, self.t)
        self.will.tick(dt, self.affect.stress, self.drives.get("energy"), self.clock.asleep)
        self.lifecycle.tick(dt)
        self.lifecycle.apply(self)
        self.persona.drift(dt, self.rng)

        action, reason = self._decide(ev, ap, salience)
        llm_request = None
        if action == "speak":
            mode = "reply"
            if ev.kind == "tick" or reason in ("impulse", "intention"):
                mode = "proactive"
            elif reason == "uncertain":
                mode = "clarify"
            llm_request = self.llm_gate.build(mode, self, salience, ap, self.intentions.top(), reason)
        if action == "speak":
            self.autonomy.on_speak(self.t)
        completed = self.goals.on_action(action, self.t, self.values)
        if completed is not None:
            self.values.observe_goal(completed, success=True)
        self.will.on_action(action == "speak", self.will.last_source)
        self.self_model.on_action(action, action == "speak")

        outcome = Outcome(
            t=round(self.t, 2),
            action=action,
            reason=reason,
            prompt_state=self.prompt_state(action, reason),
            state=self.snapshot(),
            llm_request=llm_request,
            act=self._to_action(action, reason),
        )
        if outcome.act.kind == "rest":
            self.drives.values["energy"] = clip(self.drives.values["energy"] + 0.12, 0.0, 1.0)
            self.affect.stress = clip(self.affect.stress - 0.08, 0.0, 1.0)
        elif outcome.act.kind == "seek_help":
            self.drives.values["social"] = clip(self.drives.values["social"] + 0.08, 0.0, 1.0)
        elif outcome.act.kind == "protect":
            self.drives.values["safety"] = clip(self.drives.values["safety"] + 0.08, 0.0, 1.0)
        try:
            self.autonomy.adapt(self.t, float(self.viability_state.get("viability", 1.0)),
                                float(self.drives.get("social")), float(self.affect.stress),
                                action == "speak", dt)
        except Exception:
            pass
        self.history.append(outcome)
        self.last_outcome = outcome
        return outcome


    def _workspace_candidates(self, ev: Event, ap, salience: float, hit) -> List[Candidate]:
        out: List[Candidate] = []
        if ev.text:
            out.append(Candidate(ev.text, "event", salience, self.t))
        if ap.occ_emotion:
            out.append(Candidate(ap.occ_emotion, "affect", 0.5 * abs(ap.valence) + 0.5 * ap.threat, self.t))
        goal = self.goals.top(self.values)
        if goal is not None:
            out.append(Candidate(goal.name, "goal", max(0.0, goal.priority), self.t))
        if hit is not None:
            out.append(Candidate(hit.text, "memory", max(0.0, hit.importance), self.t))
        return out

    def _state_features(self) -> Dict[str, float]:
        feats = {
            "stress": float(self.affect.stress),
            "fear": float(max(0.0, min(1.0, self.affect.stress))),
            "mood": float(self.affect.mood),
            "v": float(self.affect.v),
            "a": float(self.affect.a),
            "social": float(self.drives.get("social")),
            "energy": float(self.drives.get("energy")),
            "safety": float(self.drives.get("safety")),
            "curiosity": float(self.drives.get("curiosity")),
            "identity": float(self.self_model.identity),
            "willpower": float(self.will.willpower),
            "confidence": float(self.meta.confidence),
            "viability": float(self.viability_state.get("viability", 1.0)),
            "needs_satisfaction": float(1.0 - max(self.needs.deficits.values() or [0.0])) if hasattr(self, "needs") else 0.5,
        }
        try:
            feats.update(self.physiology.interoception(self.drives.get("energy"), self.drives.get("social")))
            for _k, _v in self.needs.deficits.items():
                feats["need_" + _k] = float(_v)
            feats.update(self.physiology.emotion_profile(self.affect.v, self.affect.stress, self.drives.get("social")))
            feats["uncertainty_entropy"] = float(self.uncertainty.last_entropy)
            feats["chaos_x"] = float(self.uncertainty.x)
        except Exception:
            pass
        env = getattr(self, "env", None)
        if isinstance(env, dict):
            for k, v in env.items():
                try:
                    feats[str(k)] = float(v)
                except Exception:
                    pass
        return feats

    def _blend_appraisal(self, ev: Event, ap):
        head = self.heads.get("appraisal")
        if head is None:
            return ap
        try:
            pred = head.predict(ev.text, self._state_features())
        except Exception:
            return ap
        b = clip(float(self.cfg.appraisal_blend), 0.0, 1.0)

        def mix(name: str, old: float) -> float:
            return (1.0 - b) * float(old) + b * float(pred.get(name, old))

        return Appraisal(
            novelty=ap.novelty,
            relevance=mix("relevance", ap.relevance),
            control=ap.control,
            rpe=ap.rpe,
            social=mix("social", ap.social),
            threat=mix("threat", ap.threat),
            valence=mix("valence", ap.valence),
            arousal=mix("arousal", ap.arousal),
            goal_relevance=ap.goal_relevance,
            goal_congruence=ap.goal_congruence,
            agency=ap.agency,
            certainty=ap.certainty,
            coping_potential=ap.coping_potential,
            norm_compliance=ap.norm_compliance,
            occ_emotion=ap.occ_emotion,
        )

    def _cognitive_prior(self, ev: Event, ap, salience: float):
        if ap.occ_emotion in ("fear", "shame", "guilt") and ap.coping_potential < 0.30:
            return "silence", "occ_low_coping"
        if ap.occ_emotion == "pride" and ap.goal_congruence > 0.5 and salience >= 0.55 and self._can_initiate():
            return "speak", "occ_pride"
        if ev.speaker and ev.speaker != "self":
            if self.tom.predict_action(ev.speaker) == "avoid" and ap.threat > 0.50:
                return "silence", "tom_avoid"
        return None

    def _action_advice(self, ev: Event):
        head = self.heads.get("action_advice") or self.heads.get("general_policy")
        if head is None:
            return None
        try:
            if hasattr(head, "advise"):
                kind, confidence = head.advise(ev.text, self._state_features())
            else:
                probs = head.predict_proba(ev.text, self._state_features())
                kind = max(probs, key=probs.get)
                confidence = float(probs[kind])
        except Exception:
            return None
        return {"kind": str(kind), "confidence": round(float(confidence), 3), "threshold": float(getattr(head, "threshold", 0.0)), "source": "action_advice"}

    def _policy_hint(self, ev: Event):
        head = self.heads.get("policy")
        general = False
        if head is None:
            head = self.heads.get("general_policy")
            general = True
        if head is None:
            return None
        try:
            probs = head.predict_proba(ev.text, self._state_features())
        except Exception:
            return None
        if not probs:
            return None
        action = max(probs, key=probs.get)
        return action, float(probs[action])

    def _world_reward(self, ev: Event, action: str):
        head = self.heads.get("world")
        if head is None:
            return None
        try:
            pred = head.predict(ev.text, self._state_features(), action)
            return float(pred.get("reward"))
        except Exception:
            return None

    def _to_action(self, action: str, reason: str) -> Action:
        if action == "forage":
            return Action(kind="forage", modality="none", style="forage")
        if action == "rest":
            return Action(kind="rest", modality="none", style="rest")
        if action == "explore":
            return Action(kind="explore", modality="none", style="explore")
        if action == "drink":
            return Action(kind="drink", modality="none", style="drink")
        if action == "heal":
            return Action(kind="heal", modality="none", style="heal")
        if action == "socialize":
            return Action(kind="approach", modality="none", style="socialize")
        if action in ("speak", "ask"):
            kind = "communicate"
            advice = self.last_action_advice
            if advice and advice.get("confidence", 0.0) >= float(advice.get("threshold", 0.0)):
                kind = advice["kind"]
            elif self.attachment.style() != "secure":
                kind = self.attachment.action_kind()
            elif getattr(self, "last_event_kind", None) == "tick" and self.circuit.behavior_bias() != "neutral":
                kind = self.circuit.behavior_bias()
            alarm = float(getattr(self, "viability_state", {}).get("alarm", 0.0))
            if alarm >= 0.01 and self.viability.dominant_deficit() in ("energy", "social", "safety"):
                kind = {"energy": "rest", "social": "seek_help", "safety": "protect"}[self.viability.dominant_deficit()]
            return Action(kind=kind, modality="text", style=action)
        return Action(kind="withdraw", modality="none", style="silence")

    def _can_initiate(self) -> bool:
        if not self.last_speaker:
            return True
        return self.social.stage(self.last_speaker) not in ("stranger", "acquaintance")

    def _decide(self, ev: Event, ap, salience: float):
        head = self.heads.get("decide")
        if head is not None:
            try:
                try:
                    probs = head.predict_proba(
                        ev.text, self._state_features(), directed=ev.directed, asleep=self.clock.asleep, kind=ev.kind
                    )
                except TypeError:
                    probs = head.predict_proba(ev.text, self._state_features())
                if probs:
                    action = max(probs, key=probs.get)
                    if action in ("speak", "silence"):
                        return action, "learned_decide"
            except Exception:
                pass
        if ev.kind == "self_action":
            return "silence", "self_action"
        gate = self.heads.get("timing_gate")
        if gate is not None and hasattr(gate, "policy_hint"):
            try:
                g_action, g_conf = gate.policy_hint(ev.text, self._state_features())
                if float(g_conf) >= float(self.cfg.policy_prior_threshold):
                    return str(g_action), "learned_timing_gate"
            except Exception:
                pass
        if ev.directed:
            return "speak", "directed"
        if self.clock.asleep:
            return "silence", "asleep"

        conflict = self.goals.conflict(self.values)
        due = self.autonomy.due(self.t, self.clock.asleep)
        impulse = 0.0
        if due:
            impulse = min(1.0, max(0.0, self.autonomy.h / max(self.autonomy.threshold, 1e-6)))
        intention = self.intentions.top()

        if ev.kind == "tick":
            energy_policy = self.heads.get("energy_policy")
            if energy_policy is not None and hasattr(energy_policy, "choose"):
                try:
                    e_action, e_conf = energy_policy.choose(self._state_features())
                    if float(e_conf) >= float(getattr(self.cfg, "energy_policy_threshold", 0.20)):
                        return str(e_action), "energy_policy"
                except Exception:
                    pass
            if self.lifecycle.sleep_debt >= 0.80 and not self.clock.asleep:
                self.sleep(4 * 3600.0)
                return "silence", "sleep_debt"
            alarm = float(getattr(self, "viability_state", {}).get("alarm", 0.0))
            if alarm >= 0.01 and self.viability.dominant_deficit() in ("energy", "social", "safety"):
                return "speak", "viability"
            if conflict["level"] == "high":
                return "silence", "goal_conflict"
            action, source = self.will.choose(impulse, intention)
            if action == "speak":
                if not self._can_initiate():
                    return "silence", "relationship_guard"
                return "speak", source
            if source == "intention":
                return "silence", "intention"
            if due:
                ai = self.active_inference.choose(
                    self.drives.allostatic_load(),
                    abs(self.self_model.prediction_error),
                    self.drives.get("social"),
                    self.drives.get("energy"),
                    self.circuit.action_activity(salience),
                    float(getattr(self, "circuit_valence", 0.0)),
                )
                if ai["action"] == "speak" and self._can_initiate():
                    return "speak", "active_inference"
            hint = self._policy_hint(ev)
            if (
                hint is not None
                and hint[1] >= self.cfg.policy_prior_threshold
                and hint[0] == "speak"
                and self._can_initiate()
            ):
                return "speak", "policy_prior"
            return "silence", "tick"

        if self.meta.should_abstain():
            return "silence", "uncertain"

        action, source = self.will.choose(impulse, intention)
        if action == "speak":
            if source in ("impulse", "intention") and not self._can_initiate():
                return "silence", "relationship_guard"
            if source in ("impulse", "intention") and conflict["level"] == "high":
                return "silence", "goal_conflict"
            return "speak", source
        if source == "intention":
            return "silence", "intention"
        prior = self._cognitive_prior(ev, ap, salience)
        if prior is not None:
            return prior
        hint = self._policy_hint(ev)
        if hint is not None and hint[1] >= self.cfg.policy_prior_threshold:
            if hint[0] == "speak" and self._can_initiate():
                return "speak", "policy_prior"
            if hint[0] == "silence":
                return "silence", "policy_prior"
        speak_r = self._world_reward(ev, "speak")
        silence_r = self._world_reward(ev, "silence")
        if salience >= 0.45 or ap.social >= 0.50:
            if speak_r is not None and silence_r is not None and silence_r > speak_r + self.cfg.world_margin:
                return "silence", "world_model"
            return "speak", "salient"
        if (
            speak_r is not None
            and silence_r is not None
            and speak_r > silence_r + self.cfg.world_margin
            and self._can_initiate()
        ):
            return "speak", "world_model"
        return "silence", "low_salience"

    def tick(self, dt: Optional[float] = None) -> Outcome:
        step = self.cfg.dt if dt is None else float(dt)
        return self.step(Event(kind="tick", ts=self.t + step))

    def run(self, events: Iterable[Event]) -> List[Outcome]:
        return [self.step(e) for e in events]

    def post_reply(
        self,
        text: str,
        feedback: Optional[float | Feedback] = None,
        speaker: Optional[str] = None,
        responded: bool = True,
        feedback_source: str = "user",
    ) -> Dict[str, object]:
        """After a reply: memory + learning + social feedback.

        feedback: float or Feedback(valence, source, confidence).
        source weights: user=1.0, llm=0.5, self=0.35; negative feedback gets 1.25x.
        responded=False means the other side stayed silent (ostracism), not "neutral".
        """
        spk = speaker or self.last_speaker or "unknown"
        if isinstance(feedback, Feedback):
            raw_fb = clip(float(feedback.valence), -1.0, 1.0)
            source = feedback.source or feedback_source
            confidence = clip(float(feedback.confidence), 0.0, 1.0)
        else:
            raw_fb = None if feedback is None else clip(float(feedback), -1.0, 1.0)
            source = feedback_source
            confidence = 1.0
        stage = self.social.stage(spk)
        relationship_trust = {
            "stranger": 1.0,
            "acquaintance": 1.1,
            "familiar": 1.2,
            "trusted": 1.3,
            "intimate": 1.4,
        }.get(stage, 1.0)
        source_weight = {"user": 1.0, "llm": 0.5, "self": 0.35}.get(source, 0.5)
        if source == "llm":
            source_weight *= relationship_trust
        if raw_fb is None or not responded:
            effective = None
        else:
            negativity = 1.25 if raw_fb < 0 else 1.0
            effective = clip(raw_fb * source_weight * negativity * confidence, -1.0, 1.0)

        if not responded:
            social_signal = -0.40
        elif effective is not None:
            social_signal = effective
        else:
            social_signal = 0.0

        repair_factor = clip(1.0 - 0.30 * self.attachment.avoidance - 0.15 * self.attachment.anxiety, 0.40, 1.0)
        self.social.on_feedback(spk, effective, responded, self.t, self.cfg.dt, repair_factor)
        self.attachment.update(social_signal, self.self_model.prediction_error, self.cfg.dt, self.affect.fatigue, self.circuit.chronic_scar)
        self.drives.values["social"] = clip(
            self.drives.values["social"] + (0.02 if responded else -0.02), 0.0, 1.0
        )
        if responded and social_signal > 0.2:
            # social buffering: positive interaction restores some energy and lowers stress
            self.drives.values["energy"] = clip(self.drives.values["energy"] + 0.01, 0.0, 1.0)
            self.affect.stress = clip(self.affect.stress - 0.01, 0.0, 1.0)
            # sustained repair slowly reduces structural scar
            self.circuit.chronic_scar = max(getattr(self.circuit, "scar_floor", 0.05), self.circuit.chronic_scar - 0.002)
        social_ap = Appraisal(
            novelty=0.0,
            relevance=0.5,
            control=0.5,
            rpe=0.0,
            social=social_signal,
            threat=max(0.0, -social_signal) * 0.3,
            valence=social_signal,
            arousal=0.5,
        )
        self.affect.update(social_ap, self.drives, self.cfg.dt)

        if text:
            self.memory.write(text, 0.25, 0.0, self.t)
        self.self_model.on_action("speak", True)
        self.autonomy.on_speak(self.t)
        learning_cue = self.last_user_text or text
        out = self.learner.on_reply(learning_cue, effective if responded else None, self.t, intrinsic=self.drives.intrinsic_reward(), dan_reward=float(self.circuit.dan_reward), dan_punishment=float(self.circuit.dan_punishment), region_gain=float(self.circuit.learning_gain()))
        self.autonomy.learn_from_feedback(float(out.get("rpe") or 0.0), self.drives.allostatic_load())
        self.active_inference.learn(float(out.get("rpe") or 0.0), self.drives.allostatic_load(), abs(self.self_model.prediction_error))
        self.circuit.learn_recovery(float(out.get("rpe") or 0.0))
        self.circuit.learn_connection(float(out.get("rpe") or 0.0))
        out["social"] = {
            "speaker": spk,
            "responded": responded,
            "signal": round(social_signal, 3),
        }
        drift_valence = -0.40 if not responded else (effective if effective is not None else 0.0)
        goal = self.goals.top(self.values)
        values_delta = self.values.observe_feedback(drift_valence, source, stage, goal)
        conflict_level = self.goals.conflict(self.values)["level"]
        if effective is not None or not responded:
            self.self_model.on_feedback(values_delta, conflict_level, drift_valence)
        out["viability"] = dict(getattr(self, "viability_state", {}))
        out["intrinsic_reward"] = round(self.drives.intrinsic_reward() + self.viability.intrinsic_reward(), 4)
        out["values_delta"] = values_delta
        out["identity"] = self.self_model.snapshot()["identity"]
        self.narrative.update(self.self_model, self.values, self.goals, self.social, self.t, spk)
        out["narrative"] = self.narrative.context()
        out["feedback"] = {
            "raw": raw_fb,
            "effective": None if effective is None else round(effective, 3),
            "source": source,
            "confidence": confidence,
            "relationship": stage,
            "source_weight": round(source_weight, 3),
        }
        return out

    def repair(self, speaker: Optional[str] = None, effort: float = 1.0) -> Dict[str, object]:
        """Apology / care: raises trust and attachment for a speaker."""
        spk = speaker or self.last_speaker
        if not spk:
            return {"speaker": None, "repaired": False}
        b = self.social.repair(spk, effort, self.t)
        self.drives.values["social"] = clip(self.drives.values["social"] + 0.02 * effort, 0.0, 1.0)
        social_ap = Appraisal(
            novelty=0.0,
            relevance=0.5,
            control=0.5,
            rpe=0.0,
            social=0.5 * effort,
            threat=0.0,
            valence=0.5 * effort,
            arousal=0.5,
        )
        self.affect.update(social_ap, self.drives, self.cfg.dt)
        return {
            "speaker": spk,
            "repaired": True,
            "stage": b.stage,
            "trust": round(b.trust, 3),
            "attachment": round(b.attachment, 3),
        }

    def inject_affect(self, valence: float, arousal: float, dominance: float = 0.0,
                      intensity: float = 0.8, blend: float = 0.7) -> Dict[str, object]:
        from .affective import inject_affect as _inject_affect
        return _inject_affect(self, valence, arousal, dominance, intensity, blend)

    def inject_emotion(self, emotion: str, intensity: float = 0.5, blend: float = 0.6) -> Dict[str, object]:
        from .affective import inject_emotion as _inject
        return _inject(self, emotion, intensity, blend=blend)

    def empathize(self, user_emotion: str, intensity: float = 0.5, empathy_gain: float = 0.35) -> Dict[str, object]:
        from .affective import empathize as _empathize
        return _empathize(self, user_emotion, intensity, empathy_gain)

    def sleep(self, duration: float = 3600.0) -> Dict[str, object]:
        """Offline consolidation / rest. Digital-fly sleep, not a metaphor."""
        self.lifecycle.sleep(self, duration)
        return self.snapshot()

    def unified_state(self) -> Dict[str, object]:
        st = unified_state(self)
        st["attachment"] = self.attachment.snapshot()
        st["circuit"] = self.circuit.snapshot()
        st["circuit_valence"] = round(float(getattr(self, "circuit_valence", 0.0)), 4)
        st["critical"] = dict(getattr(self, "critical_state", {}))
        st["viability"] = self.viability.snapshot(self.drives)
        if self.last_user_text:
            pred = self.memory.predict(self.last_user_text, self.t)
            st["memory_prediction"] = pred.text if pred else None
            st["memory_prediction_error"] = round(float(getattr(self, "memory_prediction_error", 0.0)), 4)
        return st

    def prompt_state(self, action: str, reason: str) -> Dict[str, object]:
        return {
            "mood": round(self.affect.mood, 2),
            "v": round(self.affect.v, 2),
            "a": round(self.affect.a, 2),
            "stress": round(self.affect.stress, 2),
            "drives": self.drives.snapshot(),
            "focus": self.attention.focus,
            "relationship": self.social.stage(self.last_speaker) if self.last_speaker else None,
            "confidence": round(self.meta.confidence, 2),
            "willpower": round(self.will.willpower, 2),
            "goal": self.goals.top(self.values).name if self.goals.top(self.values) else None,
            "goal_progress": self.goals.top(self.values).progress if self.goals.top(self.values) else None,
            "goal_priority": self.goals.top(self.values).priority if self.goals.top(self.values) else None,
            "goal_conflict": self.goals.conflict(self.values)["level"],
            "top_value": self.values.top().name,
            "identity": round(self.self_model.identity, 2),
            "continuity": round(self.self_model.continuity, 2),
            "agency": round(self.self_model.agency, 2),
            "narrative": self.narrative.context(),
            "tom": self.tom.context(),
            "workspace": self.workspace.context(),
            "action_advice": self.last_action_advice,
            "occ": self.last_appraisal.occ_emotion if self.last_appraisal else None,
            "coping": round(self.last_appraisal.coping_potential, 2) if self.last_appraisal else None,
            "expression_style": policy_for(self.persona).style,
            "intention": self.intentions.top().goal if self.intentions.top() else None,
            "choice_source": self.will.last_source,
            "action": action,
            "action_kind": "communicate" if action in ("speak", "ask") else "withdraw",
            "modality": "text" if action in ("speak", "ask") else "none",
            "reason": reason,
            "needs": self.needs.snapshot() if hasattr(self, "needs") else {},
            "dominant_need": self.needs.dominant() if hasattr(self, "needs") else None,
            "need_focus": (self.needs.snapshot().get("dominant") if hasattr(self, "needs") else None),
        }

    def health(self) -> Dict[str, object]:
        return invariants_health(self)

    def assert_invariants(self) -> None:
        assert_ok(self)

    def snapshot(self) -> Dict[str, object]:
        return {
            "t": round(self.t, 2),
            "drives": self.drives.snapshot(),
            "clock": self.clock.snapshot(),
            "affect": self.affect.snapshot(),
            "attention": self.attention.snapshot(),
            "wm": self.wm.snapshot(),
            "memory": self.memory.snapshot(),
            "social": self.social.snapshot(),
            "persona": self.persona.snapshot(),
            "self": self.self_model.snapshot(),
            "meta": self.meta.snapshot(),
            "autonomy": self.autonomy.snapshot(),
            "physiology": self.physiology.snapshot() if hasattr(self, "physiology") else {},
            "emotions": self.physiology.emotion_profile(self.affect.v, self.affect.stress, self.drives.get("social")) if hasattr(self, "physiology") else {},
            "needs": self.needs.snapshot() if hasattr(self, "needs") else {},
            "uncertainty": self.uncertainty.snapshot() if hasattr(self, "uncertainty") else {},
            "emotion_multi": __import__("mindbase.emotion_schema", fromlist=["emotion_readout"]).emotion_readout(self.affect.v, self.affect.a, getattr(self.affect, "dominance", 0.0)),
            "intentions": self.intentions.snapshot(),
            "will": self.will.snapshot(),
            "learning": self.learner.snapshot(),
            "mb": self.mb.snapshot(),
            "goals": self.goals.snapshot(),
            "values": self.values.snapshot(),
            "health": self.health(),
            "lifecycle": self.lifecycle.snapshot(),
            "narrative": self.narrative.snapshot(),
            "tom": self.tom.snapshot(),
            "workspace": self.workspace.snapshot(),
            "action_advice": self.last_action_advice,
            "appraisal": ({
                "occ": self.last_appraisal.occ_emotion,
                "goal_relevance": round(self.last_appraisal.goal_relevance, 3),
                "goal_congruence": round(self.last_appraisal.goal_congruence, 3),
                "agency": round(self.last_appraisal.agency, 3),
                "certainty": round(self.last_appraisal.certainty, 3),
                "coping": round(self.last_appraisal.coping_potential, 3),
                "norm": round(self.last_appraisal.norm_compliance, 3),
            } if self.last_appraisal else None),
        }
