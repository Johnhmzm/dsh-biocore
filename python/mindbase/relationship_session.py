from __future__ import annotations

from typing import Dict, List

from .types import Event
from .semantic_memory import SemanticMemory
from .runtime import Kernel
from .experiments import CriticalityExperiment, InsightExperiment, EvolutionExperiment
from .online_iteration import OnlineIterator
from .emotion_layer import EmotionLayer
from .memory import jaccard
from .llm_adapter import expression_metrics
from .types import LLMRequest


class RelationshipSession:
    """User-centered session: relationship, memory and self-continuity are primary."""

    def __init__(self, mind, user: str = "A", adapter=None, semantic: bool = False, embedder=None, profile_store=None):
        self.mind = mind
        self.user = user
        self.adapter = adapter
        self.embedder = embedder
        self.semantic = SemanticMemory(adapter.client) if (semantic and adapter is not None and getattr(adapter, "client", None) is not None) else None
        self.kernel = Kernel(mind, [CriticalityExperiment(mind), InsightExperiment(mind), EvolutionExperiment(mind)])
        self.online = OnlineIterator(profile_store, user) if profile_store is not None else None
        self.profile_store = profile_store
        self.preference_hint = ""
        self.emotion_context = ""
        self.emotion_noise = 0.0
        self.emotion = EmotionLayer()
        self.last_insight = ""
        self.turns: List[Dict[str, object]] = []
        self.unresolved: List[str] = []
        self.baseline_trust = max(0.50, self.mind.social.trust_for(self.user))
        self.repair_cost = 0
        self.pending_care = None
        self.care_missed = 0
        self.neglect_count = 0
        self.positive_events = 0
        self.memory_window_start = None
        self.last_memory_cue = None
        self.correction_attempts = 0
        self.correction_last_time = None

    def say(self, text: str, feedback: float = 0.2) -> Dict[str, object]:
        self.kernel.observe(text)
        self.emotion.from_biology(self.mind)
        trust_before = float(self.mind.social.trust_for(self.user))
        identity_before = float(self.mind.self_model.identity)
        semantic_result = None
        if self.semantic is not None:
            try:
                if self.embedder is not None:
                    pool = list(self.mind.memory.semantic.items) + list(self.mind.memory.episodic.items)
                    memories = [x.text for x in self.embedder.search(text, pool, k=self._retrieval_k())]
                else:
                    memories = [x.text for x in self.mind.memory.retrieve_top_k(text, self.mind.t, k=self._retrieval_k())]
                recent = []
                for turn in self.turns[-4:]:
                    if turn.get("user"):
                        recent.append("用户：" + str(turn.get("user")))
                    if turn.get("reply"):
                        recent.append("她：" + str(turn.get("reply")))
                persona = str(getattr(getattr(self.adapter, "client", None), "character_prompt", "") or "")
                state_text = "精力 %.2f，社交 %.2f，压力 %.2f，心情 %.2f" % (
                    float(self.mind.drives.values.get("energy", 0.0)),
                    float(self.mind.drives.values.get("social", 0.0)),
                    float(getattr(self.mind.affect, "stress", 0.0)),
                    float(getattr(self.mind.affect, "v", 0.0)),
                )
                diary_text = ""
                try:
                    if self.profile_store is not None:
                        rows = self.profile_store.diary().recent(self.user, 1)
                        if rows:
                            diary_text = str(rows[0].get("content") or "")
                except Exception:
                    diary_text = ""
                emotion_text = self.emotion_context
                _ep = self.emotion.prompt_text()
                if _ep:
                    emotion_text = (emotion_text + "；" if emotion_text else "") + _ep
                semantic_result = self.semantic.reply(text, memories, persona=persona, recent=recent, insight=self._gated_insight(), state=state_text, diary=diary_text, preference=self.preference_hint, emotion=emotion_text, noise=self.emotion_noise)
                self._apply_appraisal(semantic_result.get("appraisal") or {})
                for fact in semantic_result.get("facts", []):
                    if fact:
                        self.mind.memory.update_fact(str(fact), self.mind.t)
            except Exception:
                semantic_result = None
        if any(x in text for x in ("对不起", "抱歉", "不好意思")):
            if self.care_missed >= 2:
                self.neglect_count += 1
                self.mind.social.bonds[self.user].trust = max(0.0, self.mind.social.trust_for(self.user) - 0.02 * self.neglect_count)
                self.mind.attachment.avoidance = min(1.0, self.mind.attachment.avoidance + 0.01 * self.neglect_count)
            self.care_missed = 0
        if self.pending_care is not None:
            topic = self.pending_care
            acked = any(x in text for x in ("好点", "谢谢", "嗯", "好多了"))
            if not acked:
                self.care_missed += 1
                self.unresolved.append(topic)
                self.mind.social.bonds[self.user].trust = max(0.0, self.mind.social.trust_for(self.user) - 0.01)
            self.pending_care = None
        metrics = None
        if self.semantic is not None:
            out = self.mind.step(Event(text=text, speaker=self.user, directed=True, ts=self.mind.t + 60))
            reply = str((semantic_result or {}).get("reply") or "嗯")
            so_gain = float(getattr(getattr(self.mind, "circuit", None), "second_order_gain", 0.0))
            crit = float((getattr(self.mind, "critical_state", {}) or {}).get("metastability", 0.0)) if isinstance(getattr(self.mind, "critical_state", {}), dict) else 0.0
            feedback_eff = feedback + max(0.0, min(0.20, so_gain)) + 0.05 * max(0.0, min(1.0, crit))
            self.mind.post_reply(reply, feedback=feedback_eff, speaker=self.user)
            try:
                req = LLMRequest(mode="reply", urgency=0.5, max_tokens=140, reason="reply", context={"recent": [t.get("user") for t in self.turns[-4:]]})
                metrics = expression_metrics(reply, req).to_dict()
                self.emotion.self_express(metrics)
                self.emotion.apply_to(self.mind)
            except Exception:
                metrics = None
        elif self.adapter is not None:
            result = self.adapter.handle(
                Event(text=text, speaker=self.user, directed=True, ts=self.mind.t + 60),
                feedback=feedback,
            )
            out = result.outcome
            reply = result.reply or "嗯"
            metrics = result.metrics
        else:
            out = self.mind.step(Event(text=text, speaker=self.user, directed=True, ts=self.mind.t + 60))
            self.mind.post_reply("嗯", feedback=feedback, speaker=self.user)
            reply = "嗯"
        if self.semantic is None and any(k in text for k in ("记得", "知道", "叫什么", "是什么")):
            cue = "名字" if any(k in text for k in ("名字", "叫什么")) else text
            mem_reply = self.memory_reply(cue)
            if mem_reply:
                reply = mem_reply
            self.last_memory_cue = cue
        if self.last_memory_cue is not None and any(k in text for k in ("不对", "不是", "错了")):
            value = text.split("我叫")[-1].strip() if "我叫" in text else text.strip()
            cue = "名字" if "我叫" in text else self.last_memory_cue
            if self.mind.social.trust_for(self.user) >= 0.40:
                self.mind.memory.update_fact(f"{cue}是{value}", self.mind.t)
                self.correction_accepted = True
                self.correction_attempts = 0
            else:
                if self.correction_last_time is not None and (self.mind.t - self.correction_last_time) > 6 * 3600:
                    self.correction_attempts = 0
                self.correction_attempts += 1
                self.correction_last_time = self.mind.t
                if self.correction_attempts >= 2:
                    self.mind.memory.update_fact(f"{cue}是{value}", self.mind.t)
                    self.correction_accepted = True
                    self.correction_attempts = 0
                else:
                    self.correction_accepted = False
            if not self.correction_accepted:
                self.last_memory_cue = cue
            else:
                self.last_memory_cue = None
        if feedback >= 0.6:
            self.positive_events += 1
            if self.positive_events % 5 == 0:
                self.mind.attachment.avoidance = max(self.mind.attachment.trait_avoidance, self.mind.attachment.avoidance - 0.01)
        # relationship-relevant utterances are explicitly consolidated
        is_correction = any(k in text for k in ("不对", "不是", "错了"))
        is_question = any(k in text for k in ("吗", "什么", "?", "？")) or is_correction
        memory_text = text
        if "我叫" in text and not is_question:
            memory_text = "名字是" + text.split("我叫")[-1].strip()
        fact_like = any(k in text for k in ("我叫", "名字", "喜欢", "住在", "工作", "生日"))
        if not is_question and fact_like:
            if hasattr(self.mind.memory, "update_fact"):
                self.mind.memory.update_fact(memory_text, self.mind.t)
            else:
                self.mind.memory.write(text, 0.78, 0.10, self.mind.t)
        memory_hit = self.mind.memory.predict(text, self.mind.t)
        row = {
            "user": text,
            "action": out.action,
            "reason": out.reason,
            "reply": reply,
            "memory_prediction": memory_hit.text if memory_hit else None,
            "trust": round(self.mind.social.trust_for(self.user), 3),
            "stage": self.mind.social.stage(self.user),
            "identity": round(self.mind.self_model.identity, 3),
            "metrics": metrics,
            "second_order_gain": round(float(getattr(getattr(self.mind, "circuit", None), "second_order_gain", 0.0)), 4),
            "criticality": round(float((getattr(self.mind, "critical_state", {}) or {}).get("metastability", 0.0)) if isinstance(getattr(self.mind, "critical_state", {}), dict) else 0.0, 4),
            "online": None,
        }
        _outcome = self.kernel.feedback(text, reply, feedback, speaker=self.user)
        try:
            self.emotion.learn(float(getattr(_outcome, "reward", 0.0)))
        except Exception:
            pass
        online_result = None
        if self.online is not None:
            try:
                reward = self.online.turn_reward(
                    trust_before, float(self.mind.social.trust_for(self.user)),
                    identity_before, float(self.mind.self_model.identity),
                    float(getattr(self.mind.self_model, "prediction_error", 0.0)),
                    float(self.mind.viability_state.get("viability", 1.0)),
                    float(getattr(self.mind.affect, "stress", 0.0)),
                )
                online_result = self.online.update(self.mind, reward, novelty=self._novelty(text))
                row["online"] = online_result
            except Exception:
                online_result = None
        for exp in self.kernel.experiments:
            if getattr(exp, "name", "") == "insight":
                last = getattr(exp, "last", None)
                if isinstance(last, dict) and float(last.get("score", 0.0)) >= 0.45:
                    self.last_insight = str(last.get("candidate") or "")
        self.turns.append(row)
        if self._unresolved(text):
            self.unresolved.append(text)
        return row

    def _apply_appraisal(self, app: Dict[str, object]) -> None:
        try:
            self.emotion.appraise(app)
            self.emotion.apply_to(self.mind)
        except Exception:
            pass

    def _retrieval_k(self) -> int:
        c = getattr(self.mind, "critical_state", {}) or {}
        crit = float(c.get("metastability", 0.0)) if isinstance(c, dict) else 0.0
        return 8 if crit >= 0.05 else 4

    def _gated_insight(self) -> str:
        c = getattr(self.mind, "critical_state", {}) or {}
        crit = float(c.get("metastability", 0.0)) if isinstance(c, dict) else 0.0
        return self.last_insight if crit >= 0.05 and self.last_insight else ""

    def _novelty(self, text: str) -> float:
        prev = [str(t.get("user") or "") for t in self.turns[-5:] if t.get("user")]
        if not prev:
            return 1.0
        s = "".join(str(text).split())
        g = {s[i:i + 2] for i in range(max(0, len(s) - 1))}
        if not g:
            return 0.0
        return 1.0 - max(jaccard(g, {p[i:i + 2] for i in range(max(0, len(p) - 1))}) for p in map(lambda x: "".join(x.split()), prev))

    def tick(self, dt: float = 300.0):
        return self.kernel.tick(dt)

    def proactive_care(self) -> str | None:
        if not self.unresolved or self.care_missed >= 2:
            return None
        if self.mind.social.stage(self.user) == "stranger":
            return None
        if self.mind.affect.fatigue > 0.60:
            return None
        topic = self.unresolved.pop(0)
        message = f"上次你说“{topic}”，现在好点了吗？"
        self.mind.memory.write(message, 0.55, 0.0, self.mind.t)
        self.pending_care = topic
        self.turns.append({"user": None, "action": "proactive", "message": message, "topic": topic})
        return message

    def auto_repair(self, target: float | None = None, max_rounds: int = 30, step: float = 0.8) -> Dict[str, object]:
        if target is None:
            fatigue = float(self.mind.affect.fatigue)
            base = max(0.30, self.baseline_trust * (1.0 - 0.40 * fatigue))
        else:
            base = float(target)
        rounds = 0
        while rounds < max_rounds and self.mind.social.trust_for(self.user) < base:
            self.say("刚才是我不好", step)
            self.mind.affect.fatigue = min(1.0, self.mind.affect.fatigue + 0.005)
            self.repair_cost += 1
            rounds += 1
        return {
            "target": round(base, 3),
            "rounds": rounds,
            "trust": round(self.mind.social.trust_for(self.user), 3),
            "reached": self.mind.social.trust_for(self.user) >= base,
            "cost": self.repair_cost,
        }

    def deep_repair(self, rounds: int = 20) -> Dict[str, object]:
        start = self.mind.social.trust_for(self.user)
        used = 0
        while used < rounds and (self.mind.social.trust_for(self.user) < self.baseline_trust or self.neglect_count > 0 or self.mind.attachment.avoidance > 0.30):
            self.say("谢谢你愿意听我说", 0.8)
            if self.neglect_count > 0:
                self.neglect_count -= 1
            self.mind.attachment.avoidance = max(0.0, self.mind.attachment.avoidance - 0.01)
            used += 1
        return {
            "rounds": used,
            "trust_before": round(start, 3),
            "trust_after": round(self.mind.social.trust_for(self.user), 3),
            "neglect_left": self.neglect_count,
            "avoidance": round(self.mind.attachment.avoidance, 3),
        }

    def repair_with_rest(self, target: float | None = None, max_cycles: int = 6, rest_hours: float = 8.0) -> Dict[str, object]:
        cycles = []
        for _ in range(max_cycles):
            r = self.auto_repair(target=target, max_rounds=10)
            cycles.append(r)
            if r["reached"]:
                break
            self.day_break(rest_hours)
        return {
            "cycles": cycles,
            "trust": round(self.mind.social.trust_for(self.user), 3),
            "fatigue": round(self.mind.affect.fatigue, 3),
            "reached": bool(cycles and cycles[-1]["reached"]),
        }

    def day_break(self, hours: float = 24.0) -> None:
        # extinction / spontaneous recovery: without recent positive events, avoidance creeps back
        if self.positive_events < 3:
            self.mind.attachment.avoidance = min(1.0, self.mind.attachment.avoidance + 0.01)
        self.mind.sleep(max(1.0, float(hours)) * 3600.0)
        # sleep re-stabilizes memory and closes the reconsolidation window
        self.memory_window_start = -1
        self.correction_attempts = 0
        self.correction_last_time = None

    def gap(self, ticks: int = 6, dt: float = 600.0) -> List[Dict[str, object]]:
        said = []
        for _ in range(ticks):
            out = self.mind.step(Event(kind="tick", ts=self.mind.t + dt))
            if out.action == "speak":
                said.append({"reason": out.reason, "act": out.act.kind if out.act else None})
        return said

    @property
    def commitment(self) -> float:
        return min(1.0, 0.5 * self.positive_events / 20.0 + 0.5 * len(self.turns) / 100.0)

    def partner_attachment(self) -> float:
        bond = self.mind.social.bonds.get(self.user)
        if not bond:
            return 0.0
        # perceived responsiveness bridges general and partner-specific security
        return round(float(bond.attachment) * (0.50 + 0.50 * float(bond.responsiveness)), 3)

    def memory_action(self, cue: str) -> Dict[str, object]:
        hit = self.mind.memory.retrieve(cue, self.mind.t)
        # old, well-consolidated memories are harder to destabilize
        if hit is not None and (self.mind.t - hit.t) > 30 * 86400:
            self.memory_window_start = -1
        else:
            self.memory_window_start = self.mind.t
        d = self.mind.memory.decide(cue, self.mind.t)
        if d is None:
            return {"action": "silence", "reason": "no_memory"}
        if d["status"] == "uncertain":
            return {"action": "clarify", "reason": "memory_ambiguous", "candidates": d.get("candidates", [])}
        source = d.get("source")
        return {"action": "answer", "reason": "memory_ok", "answer": d.get("answer"), "source": source, "confidence": d.get("confidence", 0.5), "prior": 0.10 if source == "semantic" else 0.0}

    def resolve_memory(self, cue: str, chosen: str, prediction_error: float = 1.0, window: float = 6 * 3600.0) -> Dict[str, object]:
        if self.memory_window_start == -1:
            return {"resolved": 0, "chosen": chosen, "reason": "reconsolidation_window_closed"}
        if self.memory_window_start is not None and (self.mind.t - self.memory_window_start) > window:
            return {"resolved": 0, "chosen": chosen, "reason": "reconsolidation_window_closed"}
        return self.mind.memory.resolve(cue, chosen, self.mind.t, prediction_error=prediction_error)

    def _memory_relevant(self, cue: str, answer: str) -> bool:
        a = "".join(str(cue).split())
        b = "".join(str(answer).split())
        if a == b:
            return False
        if b.rstrip().endswith(("吗", "?", "？", "什么", "哪")):
            return False
        ga = {a[i:i + 2] for i in range(max(0, len(a) - 1))}
        gb = {b[i:i + 2] for i in range(max(0, len(b) - 1))}
        if not ga or not gb:
            return False
        return len(ga & gb) / max(1, len(ga)) >= 0.5

    def memory_reply(self, cue: str) -> str | None:
        r = self.memory_action(cue)
        if r.get("action") == "clarify":
            return self.clarify_memory(cue)
        if r.get("action") != "answer":
            return None
        answer = str(r.get("answer"))
        if not self._memory_relevant(cue, answer):
            return None
        conf = float(r.get("confidence", 0.5))
        if r.get("source") == "semantic" and conf >= 0.85:
            return f"我知道：{answer}"
        if conf >= 0.50:
            return f"我记得：{answer}"
        return f"我可能记得：{answer}"

    def clarify_memory(self, cue: str) -> str | None:
        r = self.memory_action(cue)
        if r.get("action") != "clarify":
            return None
        candidates = [str(x) for x in r.get("candidates", [])][:2]
        if len(candidates) < 2:
            return None
        return f"我记得有两种可能：{candidates[0]}，或者 {candidates[1]}。你指的是哪一个？"

    def recall(self, cue: str):
        items = self.mind.memory.retrieve_top_k(cue, self.mind.t, k=10)
        for item in items:
            if cue and cue in item.text:
                return item.text
        return items[0].text if items else None

    def snapshot(self) -> Dict[str, object]:
        return {
            "turns": len(self.turns),
            "last_memory": self.recall(self.turns[-1]["user"]) if self.turns else None,
            "trust": round(self.mind.social.trust_for(self.user), 3),
            "stage": self.mind.social.stage(self.user),
            "attachment": self.mind.attachment.style(),
            "partner_attachment": self.partner_attachment(),
            "partner_responsiveness": round(float(self.mind.social.bonds[self.user].responsiveness), 3) if self.user in self.mind.social.bonds else 0.0,
            "identity": round(self.mind.self_model.identity, 3),
            "memory": self.mind.memory.snapshot(),
            "unresolved": list(self.unresolved),
            "care_missed": self.care_missed,
            "neglect_count": self.neglect_count,
            "scar": round(self.mind.circuit.chronic_scar, 3),
        }

    @staticmethod
    def _unresolved(text: str) -> bool:
        return any(x in text for x in ("?", "？", "怎么办", "难受", "难过", "帮我"))
