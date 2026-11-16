# -*- coding: utf-8 -*-
"""Bridge to the digital-fly substrate (mindbase).

The substrate (mushroom body, survival gauntlet, QD/DE evolution) has lived in
its own package since the start and was never called from this runtime. This
module is the seam: it lets the core run the fly body in an environment and
evolve its genes, without putting any of that on the per-turn path.
"""
from __future__ import annotations

import math
import random
import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

_IMPORT_ERROR = None
try:  # pragma: no cover - import probe
    from mindbase import MindBase, MindConfig
    from mindbase.grid_world import EmbodiedLoop, GridWorld
    from mindbase.experiments import apply_genome, BASE_BOUNDS
except Exception as exc:  # pragma: no cover
    _IMPORT_ERROR = str(exc)[:200]


def available() -> bool:
    """True when the digital-fly substrate can be imported here."""
    return _IMPORT_ERROR is None


def unavailable_reason() -> str:
    return _IMPORT_ERROR or ""


def run_episode(genome: Optional[Sequence[float]] = None, seed: int = 1,
                steps: int = 40) -> Dict[str, float]:
    """One embodied episode in the grid world; returns the fitness inputs."""
    if _IMPORT_ERROR:
        raise RuntimeError("mindbase unavailable: " + _IMPORT_ERROR)
    mind = MindBase(MindConfig(seed=int(seed)))
    if genome:
        apply_genome(mind, tuple(genome))
    loop = EmbodiedLoop(mind, GridWorld(seed=int(seed), noise=0.1),
                        traits={"hunger_threshold": 0.5, "thirst_threshold": 0.5})
    out = loop.run(steps=int(steps)) if "steps" in loop.run.__code__.co_varnames else loop.run()
    auto = out.get("autonomy") or {}
    return {"energy": float(out.get("energy") or 0.0),
            "viability": float(out.get("viability") or 0.0),
            "initiative": float(auto.get("initiative") or 0.0),
            "ticks": int(out.get("ticks") or 0)}


def fitness(metrics: Dict[str, float]) -> float:
    """Same weights the substrate's own evolution loop uses."""
    return round(0.45 * min(1.0, metrics["energy"])
                 + 0.35 * metrics["initiative"]
                 + 0.20 * metrics["viability"], 4)


def evolve(generations: int = 3, population: int = 6, steps: int = 40,
           seed: int = 1, reward_fn=None) -> Dict[str, object]:
    """Small DE/rand/1/bin over the substrate genes.

    reward_fn(metrics) -> float lets a caller replace the stock fitness (for
    example with a dialogue-derived score) without touching this loop.
    """
    if _IMPORT_ERROR:
        raise RuntimeError("mindbase unavailable: " + _IMPORT_ERROR)
    rnd = random.Random(seed)
    bounds = [(float(lo), float(hi)) for lo, hi in BASE_BOUNDS]
    def rand_vec() -> List[float]:
        return [round(rnd.uniform(lo, hi), 4) for lo, hi in bounds]
    score_of = reward_fn or (lambda m: fitness(m))
    # DE/rand/1/bin needs three distinct donors besides the target
    pop = [rand_vec() for _ in range(max(4, int(population)))]
    best, best_fit = None, -1e9
    history = []
    for gen in range(max(1, generations)):
        fits = []
        for vec in pop:
            try:
                m = run_episode(vec, seed=seed + gen, steps=steps)
                f = float(score_of(m))
            except Exception:
                f = -1.0
            fits.append(f)
        idx = max(range(len(pop)), key=lambda i: fits[i])
        if fits[idx] > best_fit:
            best_fit, best = fits[idx], list(pop[idx])
        history.append({"gen": gen, "best": round(fits[idx], 4),
                        "mean": round(sum(fits) / len(fits), 4)})
        for i in range(len(pop)):
            a, b, c = rnd.sample([j for j in range(len(pop)) if j != i], 3)
            F = 0.5
            trial = []
            for k, (lo, hi) in enumerate(bounds):
                v = pop[a][k] + F * (pop[b][k] - pop[c][k])
                trial.append(round(max(lo, min(hi, v)), 4))
            if fits[i] <= best_fit or rnd.random() < 0.5:
                pop[i] = trial
    return {"best": best, "fitness": best_fit, "history": history,
            "bounds": bounds}


class DialogueBody:
    """One MindBase instance fed by chat turns.

    The substrate appraises structured dimensions, not sentences: text alone
    moved valence by 0.02 across clearly different messages, while the same
    texts with valence/arousal/social/control supplied moved it by 0.09 and
    split stress 0.013 vs 0.128. So the caller must appraise first.
    """

    def __init__(self, seed: int = 1, noise: bool = True):
        if _IMPORT_ERROR:
            raise RuntimeError("mindbase unavailable: " + _IMPORT_ERROR)
        from mindbase import MindBase, MindConfig
        self.mind = MindBase(MindConfig(seed=int(seed)))
        # The substrate ships an endogenous noise source (critical network,
        # 1/f OU, criticality controller) with inject(mind) built for exactly
        # this. Nothing in the runtime ever called it: idle ticks used the
        # 0.03 white noise in affect and drifted 0.035 over 200 steps, with
        # every seed choosing the same action. Wire the real source.
        # A slow baseline that the message writes into. The per-tick OU pulls
        # affect back to a target derived from the last appraisal, so a single
        # message never survives; the anchor is what an experience changes.
        self.anchor = {"valence": 0.0, "arousal": 0.5, "stress": 0.0}
        self.commit = {"open": 0.0, "kept": 0.0}
        self.integral = {"valence": 0.0, "stress": 0.0}
        # 三层（Structural Affect 式）：feeling 在 affect 里（秒级），
        # mood 是它的慢积分，load 是"偏离基线的时间累计"（只涨、极慢回落）。
        self.slow = {"mood": 0.0, "load": 0.0, "heart": 0.0}
        # 习惯化：同一个刺激反复推，力度递减；隔久了才恢复。
        # 现在按"文本前 16 字的指纹"归并，键与时间都存在 self.habit 里。
        self.habit = {}
        self.noise = None
        if noise:
            try:
                from mindbase.noise import EndogenousNoise
                self.noise = EndogenousNoise(seed=int(seed))
            except Exception:
                self.noise = None

    def _habituation(self, text: str) -> float:
        """同一个刺激重复出现时的力度系数（1 = 全新，越小越钝）。"""
        try:
            key = re.sub(r"\s+", "", str(text or ""))[:16]
            if not key:
                return 1.0
            now = time.time()
            row = self.habit.get(key) or {"n": 0.0, "ts": now}
            # 次数按时间半衰：隔得久了，反应恢复
            gap_h = max(0.0, now - float(row.get("ts") or now)) / 3600.0
            n = float(row.get("n") or 0.0) * math.exp(-gap_h / max(0.1, eff("hab_tau_h", HAB_TAU_H)))
            factor = 1.0 / (1.0 + eff("hab_gain", HAB_GAIN) * n)
            self.habit[key] = {"n": n + 1.0, "ts": now}
            # 只留最近 40 个刺激，防止无限增长
            if len(self.habit) > 40:
                keep = sorted(self.habit.items(), key=lambda kv: kv[1].get("ts", 0))[-40:]
                self.habit = dict(keep)
            return max(0.15, factor)
        except Exception:
            return 1.0

    def _tick_once(self, dt: float = 60.0) -> None:
        if self.noise is not None:
            try:
                self.noise.step(dt=float(dt))
                # Drives are the integrator; affect is recomputed from its
                # target every tick, so a push there is erased within one
                # step (0.9 -> 0.02). EndogenousNoise.inject writes affect,
                # which is why wiring it changed nothing: the numbers were
                # identical at gain 0.18, 0.6 and 1.2 with 1x and 3x calls.
                nv = float(self.noise.network.last_noise)
                for name in ("energy", "social", "safety", "curiosity"):
                    cur = float(self.mind.drives.values.get(name, 0.5))
                    val = cur + eff("noise_amp", BODY_NOISE_AMP) * nv * 0.5
                    self.mind.drives.values[name] = max(0.0, min(1.0, val))
            except Exception:
                pass
        # 冲进去的情绪必须留得住：tick() 会按目标重算 affect，
        # 一次强负面（-0.8）在一轮之内就被抹平（实测 -0.15 → +0.045）。
        # 所以先记下当前值，tick 之后按惯性并回去，而不是被覆盖。
        try:
            _aff_before = self.mind.affect
            _v_before = float(getattr(_aff_before, "v", 0.0))
            _a_before = float(getattr(_aff_before, "a", 0.5))
        except Exception:
            _v_before = _a_before = None
        self.mind.tick()
        if _v_before is not None:
            try:
                _keep = eff("push_keep", PUSH_KEEP)
                _aff_now = self.mind.affect
                _aff_now.v = max(-1.0, min(1.0,
                    float(getattr(_aff_now, "v", 0.0)) * (1.0 - _keep) + _v_before * _keep))
                _aff_now.a = max(0.0, min(1.0,
                    float(getattr(_aff_now, "a", 0.5)) * (1.0 - _keep) + _a_before * _keep))
            except Exception:
                pass
        # 慢层在这里推进：mood 跟 feeling 走但慢得多，load 记偏离的累计。
        try:
            _v = float(getattr(self.mind.affect, "v", 0.0))
            _mood = float(self.slow.get("mood") or 0.0)
            # mood 是"小时到天"的泄漏积分（Mood as integrated advantage）。
            # 按每拍固定百分比更新会让它二十分钟就追平，那等于把 mood 做成了心。
            _mood_tau = max(60.0, eff("mood_tau_s", MOOD_TAU_S))
            # 上升与消退分开：默认 rise_tau=0 时等价于原来的对称泄漏。
            _mood = leak_step(_mood, _v, dt, _mood_tau,
                              eff("rise_tau_s", 0.0),
                              eff("neg_fade_mult", NEG_FADE_MULT))
            _load = float(self.slow.get("load") or 0.0)
            _base = float(self.anchor.get("valence") or 0.0)
            # 只有“低于基线”才算欠账：高兴不该累积负荷，平静时它该慢慢退。
            _deficit = max(0.0, -_v)  # 以中性为参照：低于 0 才算欠账
            _load = (_load * (1.0 - eff("load_decay", LOAD_DECAY))
                     + _deficit * eff("load_gain", LOAD_GAIN))
            self.slow["mood"] = round(max(-1.0, min(1.0, _mood)), 5)
            self.slow["load"] = round(max(0.0, min(1.0, _load)), 5)
        except Exception:
            pass
        # 心是时间连续的：它不跟着单句跳，而是按一条时间常数去追蘑菇体的读数。
        # 追的是时间，不是消息条数 —— 隔得久就多走一点，连着说就少走一点。
        try:
            _raw = None
            try:
                _c = getattr(self.mind, "circuit", None)
                if _c is not None:
                    _raw = float(_c.valence())
            except Exception:
                _raw = None
            if _raw is not None:
                _tau = max(1.0, eff("heart_tau_s", HEART_TAU_S))
                _step = 1.0 - math.exp(-max(0.0, float(dt)) / _tau)
                _h = float(self.slow.get("heart") or 0.0)
                self.slow["heart"] = round(_h + (_raw - _h) * _step, 5)
        except Exception:
            pass
        except Exception:
            pass
        try:
            a = self.anchor
            aff = self.mind.affect
            # PI 控制：比例项负责快速回拉，积分项消除持续扰动留下的稳态误差；
            # 积分限幅（anti-windup）防止长时间偏差把积分项推到饱和。
            for key, getter, setter, lo, hi in (
                ("valence", lambda: aff.v, lambda v: setattr(aff, "v", v), -1.0, 1.0),
                ("stress", lambda: aff.stress, lambda v: setattr(aff, "stress", v), 0.0, 1.0),
            ):
                cur = float(getter())
                err = float(a[key]) - cur
                self.integral[key] = float(self.integral.get(key, 0.0)) + err * PI_KI_DT
                self.integral[key] = max(-PI_I_LIMIT, min(PI_I_LIMIT, self.integral[key]))
                u = PI_KP * err + PI_KI * self.integral[key]
                new = cur + u
                setter(max(lo, min(hi, new)))
        except Exception:
            pass

    def apply_outputs(self, outs, scale: float = 1.0) -> int:
        """把 8 个评价输出分别落到身体字段上（而不是只推一个效价）。

        之前只有一个 push_gain：所有维度压成一个推力，所以「被骂伤到的是信任
        还是自尊」在身体里根本不存在。这里让每个输出走自己的通道。
        """
        applied = 0
        if not isinstance(outs, dict):
            return 0
        aff = self.mind.affect
        try:
            # 每个字段自己的范围：stress/a 是 0..1，v/d 是 -1..1。
            for key, field, lo, hi in (("v", "v", -1.0, 1.0),
                                       ("a", "a", 0.0, 1.0),
                                       ("stress", "stress", 0.0, 1.0),
                                       ("d", "dominance", -1.0, 1.0)):
                if key in outs:
                    cur = float(getattr(aff, field, 0.0))
                    setattr(aff, field,
                            max(lo, min(hi, cur + float(outs[key]) * scale)))
                    applied += 1
        except Exception:
            pass
        try:
            drives = self.mind.drives.values
            for key, field, sign in (("social", "social", 1.0),
                                     ("trust", "safety", 1.0),
                                     ("conflict", "safety", -1.0)):
                if key in outs:
                    cur = float(drives.get(field, 0.5))
                    drives[field] = max(0.0, min(
                        1.0, cur + sign * float(outs[key]) * scale))
                    applied += 1
        except Exception:
            pass
        try:
            if "self_esteem" in outs:
                sm = self.mind.self_model
                cur = float(getattr(sm, "agency", 0.5))
                sm.agency = max(0.0, min(1.0, cur + float(outs["self_esteem"]) * scale))
                applied += 1
        except Exception:
            pass
        return applied

    def apply_dims_channels(self, dims, scale: float = 1.0) -> int:
        """7 维在 outs 之外的直连通道（只写 outs 覆盖不到的量）。

        blame/novelty/hit/involve/goal 已经由 appraisal.outs 落到 affect/drives
        （见 V2_WEIGHTS），这里不重复写；control 没有 agency 的输出、certainty
        没有 confidence 的输出，才在这里补两条。
        """
        if not isinstance(dims, dict):
            return 0
        applied = 0
        try:
            sm = self.mind.self_model
            control = dims.get("control")
            if isinstance(control, (int, float)):
                cur = float(getattr(sm, "agency", 0.5))
                sm.agency = max(0.0, min(1.0, cur + (float(control) - 0.5)
                                           * DIMS_GAIN * scale))
                applied += 1
            certainty = dims.get("certainty")
            if isinstance(certainty, (int, float)):
                cur = float(getattr(sm, "expression_confidence", 0.5))
                sm.expression_confidence = max(
                    0.0, min(1.0, cur + (float(certainty) - 0.5)
                             * DIMS_GAIN * scale))
                applied += 1
        except Exception:
            pass
        return applied

    def apply_affiliation_channels(self, dims, scale: float = 1.0) -> int:
        """closeness + needs.belonging/self_esteem/control/meaningful -> drives/self。

        closeness 与 needs.belonging 都指向 social 驱动（关系足够近、归属被满足）；
        needs.control -> agency；needs.self_esteem -> identity（自我价值）； 
        needs.meaningful_existence -> curiosity（意义感驱动探索）。
        """
        if not isinstance(dims, dict):
            return 0
        applied = 0
        try:
            drives = self.mind.drives.values
            sm = self.mind.self_model

            def _push(current, delta):
                return max(0.0, min(1.0, float(current) + delta))

            for key, field in (("closeness", "social"),
                               ("need_belonging", "social")):
                value = dims.get(key)
                if isinstance(value, (int, float)):
                    drives[field] = _push(drives.get(field, 0.5),
                                          (float(value) - 0.5) * LINK_GAIN * scale)
                    applied += 1
            value = dims.get("need_control")
            if isinstance(value, (int, float)):
                sm.agency = _push(getattr(sm, "agency", 0.5),
                                  (float(value) - 0.5) * LINK_GAIN * scale)
                applied += 1
            value = dims.get("need_self_esteem")
            if isinstance(value, (int, float)):
                sm.identity = _push(getattr(sm, "identity", 0.7),
                                    (float(value) - 0.5) * LINK_GAIN * scale)
                applied += 1
            value = dims.get("need_meaningful")
            if isinstance(value, (int, float)):
                drives["curiosity"] = _push(
                    drives.get("curiosity", 0.5),
                    (float(value) - 0.5) * LINK_GAIN * scale)
                applied += 1
        except Exception:
            pass
        return applied

    def apply_intero_channels(self, dims, scale: float = 1.0) -> int:
        """内感受四量 -> affect/physiology/drives（各选一个不重复的落点）。

        tension -> affect.fatigue（紧张耗能）；
        warmth   -> physiology.oxytocin（温暖=催产素）；
        heaviness -> drives.energy（沉=精力低）；
        clarity  -> mind.meta.confidence（清楚=元认知置信）。
        stress/social 已有状态层来源，这里不重复写。
        """
        if not isinstance(dims, dict):
            return 0
        applied = 0
        try:
            aff = self.mind.affect
            value = dims.get("intero_tension")
            if isinstance(value, (int, float)):
                aff.fatigue = max(0.0, min(1.0, float(aff.fatigue)
                                           + (float(value) - 0.3) * INTERO_GAIN * scale))
                applied += 1
            value = dims.get("intero_warmth")
            if isinstance(value, (int, float)):
                phys = self.mind.physiology
                phys.oxytocin = max(0.0, min(1.0, float(phys.oxytocin)
                                             + (float(value) - 0.6) * INTERO_GAIN * scale))
                applied += 1
            value = dims.get("intero_heaviness")
            if isinstance(value, (int, float)):
                drives = self.mind.drives.values
                drives["energy"] = max(0.0, min(1.0, float(drives.get("energy", 0.5))
                                                - (float(value) - 0.3) * INTERO_GAIN * scale))
                applied += 1
            value = dims.get("intero_clarity")
            if isinstance(value, (int, float)):
                meta = getattr(self.mind, "meta", None)
                if meta is not None:
                    cur = float(getattr(meta, "confidence", 0.5))
                    meta.confidence = max(0.0, min(1.0, cur + (float(value) - 0.7)
                                                     * INTERO_GAIN * scale))
                    applied += 1
        except Exception:
            pass
        return applied

    def feed(self, text: str, dims: Optional[Dict[str, float]] = None,
             ticks: int = 1, outs=None) -> Dict[str, float]:
        """Advance the body by one user message."""
        from mindbase.types import Event
        d = dims or {}
        # Tick first, push last. tick() re-derives affect from its target every
        # call, so a push applied before it is erased within the same message
        # (that is how 18 hours of drift vanished on one greeting).
        for _ in range(max(0, int(ticks))):
            self._tick_once()
        if d:
            # 情绪必须先被蘑菇体"感知"一次，否则它只在心跳里更新，
            # 对话中的情绪对蘑菇体等于不存在（mb_valence 一直贴着 0）。
            try:
                _v = float(d.get("valence") or 0.0)
                _intero = None
                try:
                    st = self.state()
                    _intero = {"tension": float(st.get("stress") or 0.0),
                               "warmth": float(d.get("social") or 0.5),
                               "heaviness": max(0.0, -_v),
                               "clarity": float(d.get("certainty") or 0.5)}
                except Exception:
                    _intero = None
                # 注意键名：蘑菇体读的是 v / a / stress，而评价给的是
                # valence / arousal —— 直接传 d 会让它读到全 0，等于没接。
                _aff = {"v": _v,
                        "a": float(d.get("arousal") or 0.5),
                        "stress": float(d.get("stress") or 0.0)}
                # 七套系统这一拍的 bias 与 DAN：bias 来自本次评价的数值，
                # DAN 来自"上一拍 → 这一拍"的可观测差分。
                # 整份 dims 都要传：SEEKING 要 goal/novelty、FEAR 要 certainty/blame、
                # RAGE 要 blame，只传 PAD 会让这几条通道全靠默认值（实测两句话
                # FEAR/CARE 一模一样，就是这个原因）。
                _bias_dims = dict(d)
                _bias_dims.setdefault("closeness", float(d.get("closeness") or 0.5))
                _sys_bias = system_bias(_bias_dims, self.state())
                _sys_dan = system_dan(self.slow.get("sys_seen") or {},
                                      self.state())
                _act = self.mind.circuit.update(
                    max(0.0, min(1.0, abs(_v))),
                    reward=max(0.0, _v), punishment=max(0.0, -_v),
                    intero=_intero, affect=_aff,
                    systems={"bias": _sys_bias, "dan": _sys_dan,
                             "off": [] if LUST_ENABLED else ["LUST"]})
                # 七套系统的读数落进慢层（扁平键由 state() 暴露，跨进程活）
                try:
                    _sys_now = dict(getattr(self.mind.circuit, "systems", {}) or {})
                    if _sys_now:
                        self.slow["systems"] = _sys_now
                except Exception:
                    pass
                # 心跟着走，但只走一小步：它追的是时间，不是这一句。
                try:
                    _raw = float(self.mind.circuit.valence())
                    _tau = max(1.0, eff("heart_tau_s", HEART_TAU_S))
                    _h = float(self.slow.get("heart") or 0.0)
                    self.slow["heart"] = round(
                        _h + (_raw - _h) * (1.0 - math.exp(-60.0 / _tau)), 5)
                except Exception:
                    pass
            except Exception:
                pass
            try:
                self.anchor["valence"] = round(
                    self.anchor["valence"] * (1 - ANCHOR_LEARN)
                    + float(d.get("valence") or 0.0) * ANCHOR_LEARN, 4)
                self.anchor["stress"] = round(
                    self.anchor["stress"] * (1 - ANCHOR_LEARN)
                    + max(0.0, -float(d.get("valence") or 0.0)) * 0.6 * ANCHOR_LEARN, 4)
            except Exception:
                pass
            try:
                kind = str(d.get("commitment") or "none")
                if kind == "made":
                    self.commit["open"] = self.commit["open"] + 1.0
                elif kind == "kept":
                    self.commit["kept"] = self.commit["kept"] + 1.0
                    self.commit["open"] = max(0.0, self.commit["open"] - 1.0)
            except Exception:
                pass
            try:
                _hab = self._habituation(text)
                # 参照点（文献里的"情境性"）：同一个事件，在低潮里和在平稳里推的量
                # 不一样。离参照点越远，推得越少 —— 地板效应，不是 bug。
                try:
                    _mood_now = float(self.slow.get("mood") or 0.0)
                    _v_in = float(d.get("valence") or 0.0)
                    _dist = abs(_v_in - _mood_now)
                    _hab = _hab * max(0.35, min(1.0, 1.0 - 0.45 * _dist))
                except Exception:
                    pass
                self.mind.inject_affect(float(d.get("valence") or 0.0),
                                        float(d.get("arousal") or 0.5),
                                        float(d.get("dominance") or 0.0),
                                        intensity=eff("push_gain", PUSH_GAIN) * _hab,
                                        blend=eff("push_gain", PUSH_GAIN) * _hab)
                # 只喂 PAD 不够：压力、社交需求、信任、冲突都在状态层算过，
                # 却从没进过身体。这里按同一套增益并进去，让主动推断看得到它们。
                _k = eff("push_gain", PUSH_GAIN) * _hab
                _aff3 = self.mind.affect
                _stress_in = d.get("stress")
                if isinstance(_stress_in, (int, float)):
                    _aff3.stress = max(0.0, min(1.0,
                        float(_aff3.stress) * (1.0 - _k) + float(_stress_in) * _k))
                try:
                    _drv = self.mind.drives.values
                    _social_in = d.get("social")
                    if isinstance(_social_in, (int, float)):
                        _cur = float(_drv.get("social", 0.5))
                        _drv["social"] = max(0.0, min(1.0,
                            _cur * (1.0 - _k) + float(_social_in) * _k))
                    _trust_in = d.get("trust")
                    if isinstance(_trust_in, (int, float)):
                        _cur = float(_drv.get("safety", 0.5))
                        _drv["safety"] = max(0.0, min(1.0,
                            _cur * (1.0 - _k) + (1.0 - float(_trust_in)) * _k))
                    _conf_in = d.get("conflict")
                    if isinstance(_conf_in, (int, float)):
                        _cur = float(_drv.get("safety", 0.5))
                        _drv["safety"] = max(0.0, min(1.0, _cur - float(_conf_in) * _k))
                except Exception:
                    pass
            except Exception:
                pass
        else:
            # 没有评价的普通轮次走的是 mind.step()，它同样会把 affect 重算回目标。
            # 这里也把上一轮的值按惯性并回去，否则一次冲击只活到下一句话。
            try:
                _v_keep = float(getattr(self.mind.affect, "v", 0.0))
                _a_keep = float(getattr(self.mind.affect, "a", 0.5))
            except Exception:
                _v_keep = None
            self.mind.step(Event(text=str(text or ""), speaker="user",
                                 kind="message", domain="chat"))
            if _v_keep is not None:
                try:
                    _k = eff("push_keep", PUSH_KEEP)
                    aff2 = self.mind.affect
                    aff2.v = max(-1.0, min(1.0, float(aff2.v) * (1.0 - _k) + _v_keep * _k))
                    aff2.a = max(0.0, min(1.0, float(aff2.a) * (1.0 - _k) + _a_keep * _k))
                except Exception:
                    pass
        # 评价矩阵的 8 个输出在 push 之后落地：feed 先 tick 后 push，放在 push
        # 之后才不会被 tick 重算抹平；v/a 复用 _tick_once 里已有的 PUSH_KEEP
        # 惯性，drives（safety/social）与 self_model.agency 是积分量，跨轮保留。
        if outs:
            try:
                self.apply_outputs(outs, scale=eff("outs_gain", OUTS_GAIN))
            except Exception:
                pass
        # 7 维里 outs 覆盖不到的两条（control/agency、certainty/confidence）
        if d:
            try:
                self.apply_dims_channels(d, scale=eff("dims_gain", 1.0))
            except Exception:
                pass
            try:
                self.apply_affiliation_channels(d, scale=eff("link_gain", 1.0))
            except Exception:
                pass
            try:
                self.apply_intero_channels(d, scale=eff("intero_gain", 1.0))
            except Exception:
                pass
        # 留一份"这一拍结束时身体长什么样"，下一拍拿它算七系统的 DAN
        try:
            _seen = self.state()
            self.slow["sys_seen"] = {k: float(_seen.get(k) or 0.0) for k in
                                     ("valence", "stress", "social_drive",
                                      "drive_curiosity", "agency")}
        except Exception:
            pass
        return self.state()

    def reply(self, text: str = "", ticks: int = 0) -> Dict[str, float]:
        """Record her own utterance (what she said, not what she heard)."""
        try:
            self.mind.post_reply(str(text or ""))
        except Exception:
            pass
        for _ in range(max(0, int(ticks))):
            self._tick_once()
        return self.state()

    def idle(self, ticks: int = 1) -> Dict[str, float]:
        """Pass time with no input: this is where the noise shows up."""
        for _ in range(max(0, int(ticks))):
            self._tick_once()
        return self.state()

    def state(self) -> Dict[str, float]:
        st = self.mind.unified_state()
        A = st.get("A") or {}
        ai = st.get("active_inference") or {}
        self_ = st.get("self") or {}
        dr = (st.get("drives") or {}).get("values") or {}
        out = {"valence": round(float(A.get("valence") or 0.0), 4),
               "arousal": round(float(A.get("arousal") or 0.0), 4),
               "stress": round(float(A.get("stress") or 0.0), 4),
               "mood": round(float(A.get("mood") or 0.0), 4),
               "agency": round(float(self_.get("agency") or 0.0), 4),
               "social_drive": round(float(dr.get("social") or 0.0), 4),
               "drive_safety": round(float(dr.get("safety") or 0.5), 4),
               "drive_energy": round(float(dr.get("energy") or 0.5), 4),
               "drive_curiosity": round(float(dr.get("curiosity") or 0.5), 4),
               "action": str(ai.get("action") or ""),
               "anchor_valence": round(float(self.anchor["valence"]), 4),
               "anchor_stress": round(float(self.anchor["stress"]), 4),
               "commit_open": float(self.commit["open"]),
               "commit_kept": float(self.commit["kept"]),
               "mood": round(float(self.slow.get("mood") or 0.0), 4),
               "allostatic_load": round(float(self.slow.get("load") or 0.0), 4)}
        # 蘑菇体是唯一的情绪器官：状态里的 mb_emotion 是它的读数；扁平字段
        # 为了跨进程落盘（load_state 只收数字与 action）。
        try:
            circuit = getattr(self.mind, "circuit", None)
            if circuit is not None:
                approach = max(0.0, float(getattr(circuit, "mbon_approach", 0.0)))
                avoid = max(0.0, float(getattr(circuit, "mbon_avoid", 0.0)))
                valence = float(circuit.valence())
                reward = max(0.0, float(getattr(circuit, "dan_reward", 0.0)))
                punishment = max(0.0, float(getattr(circuit, "dan_punishment", 0.0)))
                # 心不设限：蘑菇体的读数原样报出，不夹到 [0,1] 或 [-1,1]。
                # 限制是身体的属性（它得在量程里），不是心的。
                arousal = abs(approach - avoid) + 0.5 * (reward + punishment)
                bias = 0
                try:
                    bias_value = circuit.behavior_bias()
                    bias = {"approach": 1, "avoid": -1}.get(bias_value, 0)
                except Exception:
                    bias = 0
                out["mb_emotion"] = {
                    "approach": round(approach, 4),
                    "avoid": round(avoid, 4),
                    "valence": round(valence, 4),
                    "arousal": round(arousal, 4),
                    "reward": round(reward, 4),
                    "punishment": round(punishment, 4),
                    "bias": bias,
                }
                # 心 = 蘑菇体读数的时间连续版本（低通）；原始读数仍留在 mb_emotion 里
                out["heart"] = round(float(self.slow.get("heart") or 0.0), 4)
                # 七套系统：扁平数字键（save_state 只收数字，嵌套 dict 会丢），
                # 这样它们能跨进程活下来，心跳与对话读的是同一份。
                _sys = self.slow.get("systems") if isinstance(
                    self.slow.get("systems"), dict) else {}
                for _name in SYSTEMS:
                    if isinstance(_sys.get(_name), (int, float)):
                        out["sys_" + _name] = round(float(_sys[_name]), 4)
                out["mb_approach"] = round(approach, 4)
                out["mb_avoid"] = round(avoid, 4)
                out["mb_valence"] = round(valence, 4)
                out["mb_arousal"] = round(arousal, 4)
                out["mb_bias"] = int(bias)
        except Exception:
            pass
        return out


# 评价七维：渲染层（显/隐）与 7x7 评价矩阵都要这几个数。
APPRAISAL_DIMS = ("goal", "blame", "control", "certainty", "novelty",
                  "involve", "hit")
# 模型"真的产出了评价"的证据：只有这六个键是 PAD 里没有的。
# control 两边都有，不能拿它当证据，否则老 prompt 的返回会被误判成 model。
_APPRAISAL_NEW = ("goal", "blame", "certainty", "novelty", "involve", "hit")


def _clamp_num(value, lo, hi, default=0.0):
    try:
        return max(lo, min(hi, float(value)))
    except Exception:
        return float(default)


def fallback_appraisal(dims=None, state=None):
    """模型不可用时的七维：只用已有数值反推，**不含任何词表**。

    映射（粗糙，但方向可解释，全部有界）：
      goal      = valence × relevance          事情顺不顺，看情绪好坏乘相关度
      blame     = max(0,−valence) × relevance  被怪责，只看负向强度乘相关度
      control   = control（模型给过就用，否则 0.5）
      certainty = 0.5 + (relevance−0.5)×0.4    越相关越容易摸准（弱先验）
      novelty   = arousal × 0.7                越激动越像"出乎意料"
      involve   = relevance                    被牵涉程度 = 相关度
      hit       = max(0,−valence) × relevance  戳到痛处 ≈ 负向 × 相关度
    """
    d = dims if isinstance(dims, dict) else {}
    st = state if isinstance(state, dict) else {}
    core = st.get("core") if isinstance(st.get("core"), dict) else {}
    body = st.get("body") if isinstance(st.get("body"), dict) else {}

    def _pick(*keys):
        for src in (d, core, body, st):
            if not isinstance(src, dict):
                continue
            for key in keys:
                if isinstance(src.get(key), (int, float)) and not isinstance(src.get(key), bool):
                    return float(src[key])
        return None

    v = _pick("valence", "v")
    v = 0.0 if v is None else _clamp_num(v, -1.0, 1.0)
    a = _pick("arousal", "a")
    a = 0.5 if a is None else _clamp_num(a, 0.0, 1.0)
    rel = _pick("relevance")
    if rel is None:
        # 没有相关度时给一个"被这件事影响得越深，就越相关"的下限：
        # 被推到 −0.9 说明这一下确实落在她身上。纯数值，不读字面。
        rel = max(0.5, -v) if v < 0 else 0.5
    rel = _clamp_num(rel, 0.0, 1.0)
    ctrl = _pick("control")
    ctrl = 0.5 if ctrl is None else _clamp_num(ctrl, 0.0, 1.0)
    neg = max(0.0, -v)
    return {"goal": round(_clamp_num(v * rel, -1.0, 1.0), 4),
            "blame": round(_clamp_num(neg * rel * 0.9, 0.0, 1.0), 4),
            "control": round(ctrl, 4),
            "certainty": round(_clamp_num(0.5 + (rel - 0.5) * 0.4, 0.0, 1.0), 4),
            "novelty": round(_clamp_num(a * 0.7, 0.0, 1.0), 4),
            "involve": round(rel, 4),
            "hit": round(_clamp_num(neg * rel, 0.0, 1.0), 4)}


def complete_appraisal(dims=None, state=None, source=None):
    """补全七维并标来源：返回 (dims, "model" | "fallback")。

    模型给了几维就留几维，缺的用数值反推补上；只要模型产出了
    _APPRAISAL_NEW 里任何一维，来源就记 model（其余是补的），
    否则整组都算 fallback。来源会进注入文本，读的人一眼能看出这行字是谁算的。
    """
    data = dict(dims) if isinstance(dims, dict) else {}
    model_hits = [k for k in _APPRAISAL_NEW
                  if isinstance(data.get(k), (int, float))
                  and not isinstance(data.get(k), bool)]
    fb = fallback_appraisal(data, state)
    out = dict(data)
    for key in APPRAISAL_DIMS:
        if key in fb and not isinstance(out.get(key), (int, float)):
            out[key] = fb[key]
    if source in ("model", "fallback"):
        src = source
    else:
        src = "model" if model_hits else "fallback"
    return out, src


def appraise_text(text: str, config: Optional[Dict[str, object]] = None,
                  timeout: float = 60.0, opener=None) -> Dict[str, float]:
    """用配置里的小模型把一句话评成数字：PAD 六项 + 评价七维。

    这是下行唯一的"语言→数字"关口（核心契约：prompt 是数据，不是逻辑）。
    prompt 与解析在同一个函数里，prompt 文本在 prompts_default.json 的
    appraise_dims 键（可被 data/prompts.json 覆盖）。
    失败返回 {}，调用方用 complete_appraisal 的数值反推兜底。
    opener：测试注入用（默认 urllib.request.urlopen）；不打补丁，避免与别的
    测试互相污染全局 urllib 状态。
    """
    cfg = dict(config or {})
    base = str(cfg.get("base_url") or "").rstrip("/")
    model = str(cfg.get("model") or "")
    key = str(cfg.get("key") or cfg.get("api_key") or "")
    if not (base and model and key):
        return {}
    prompt = prompt_text("appraise_dims") or prompt_text("appraise")
    import json as _json
    import re as _re
    import urllib.request as _url
    body = _json.dumps({"model": model, "temperature": 0,
                        "messages": [{"role": "system", "content": prompt},
                                     {"role": "user", "content": str(text or "")[:1200]}]}).encode()
    req = _url.Request(base + "/chat/completions", data=body,
                       headers={"Authorization": "Bearer " + key,
                                "Content-Type": "application/json"})
    _open = opener if callable(opener) else _url.urlopen
    try:
        with _open(req, timeout=timeout) as resp:
            raw = _json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        m = _re.search(r"\{.*\}", raw, _re.S)
        data = _json.loads(m.group(0)) if m else {}
    except Exception:
        return {}
    out = {}
    if str(data.get("commitment") or "") in ("made", "kept", "none"):
        out["commitment"] = str(data["commitment"])
    for name in ("valence", "arousal", "dominance", "relevance", "social", "control"):
        if name in data:
            try:
                out[name] = float(data[name])
            except Exception:
                continue
    # 七维与 PAD 同一次调用返回；缺哪维就不写哪维，交给 complete_appraisal 补。
    for name in APPRAISAL_DIMS:
        if name in data:
            lo = -1.0 if name == "goal" else 0.0
            try:
                out[name] = round(max(lo, min(1.0, float(data[name]))), 4)
            except Exception:
                continue
    return out


BODY_STATE_FILE = "mindbase_body.json"
RECALL_DIGEST_FILE = "recall_digest.txt"
import os as _os
# 0.30 is where spontaneous choice appears: at 200 idle ticks, 3 of 12 seeds
# pick silence instead of speak. Below 0.15 every seed does the same thing.
BODY_NOISE_AMP = float(_os.environ.get("BIOCORE_BODY_NOISE_AMP") or 0.30)
# how hard one message pushes the body: 1.0 = replace, 0.35 = nudge
PUSH_GAIN = float(_os.environ.get("BIOCORE_BODY_PUSH") or 0.35)
# appraisal.outputs 已经是逐项限幅后的 delta（CAPS），直接落、不再额外打折；
# 需要更钝的近似旧行为可用 BIOCORE_OUTS_GAIN=0.35。
OUTS_GAIN = float(_os.environ.get("BIOCORE_OUTS_GAIN") or 1.0)
# appraisal 的 7 维里，outs 覆盖不到的直连通道：control -> agency、certainty -> confidence。
DIMS_GAIN = float(_os.environ.get("BIOCORE_DIMS_GAIN") or 0.2)
# 关系亲近 + 四需求 -> drives/self_model（只在状态层算过、此前没进身体）
LINK_GAIN = float(_os.environ.get("BIOCORE_LINK_GAIN") or 0.2)
# 内感受四量 -> 找各自没有第二条来源的落点（不重复写 stress/social）
INTERO_GAIN = float(_os.environ.get("BIOCORE_INTERO_GAIN") or 0.2)
ANCHOR_GAIN = float(_os.environ.get("BIOCORE_ANCHOR_GAIN") or 0.04)
ANCHOR_LEARN = float(_os.environ.get("BIOCORE_ANCHOR_LEARN") or 0.25)
# 一次冲击在下一轮还能留住多少：0 = 立刻抹平（旧行为），0.7 = 三轮左右退干净
PUSH_KEEP = float(_os.environ.get("BIOCORE_PUSH_KEEP") or 0.7)
# 慢层参数：mood 每轮只跟 feeling 走一小步；load 是偏离的累计，回得极慢。
MOOD_ALPHA = float(_os.environ.get("BIOCORE_MOOD_ALPHA") or 0.05)
# 欠账（allostatic load）：统一到"按时间"那套（与 pipeline 的 LOAD_TAU_S 同源）。
# 旧的按拍常数 0.15/0.002 会让负载顶到 1.0 饱和、峰后 3537 拍才回零（实测），
# 没有梯度；按时间那套均值 0.314、极大 0.675、峰后 60 拍回零。
# 想回到旧行为：BIOCORE_LOAD_GAIN=0.15 BIOCORE_LOAD_DECAY=0.002
LOAD_GAIN = float(_os.environ.get("BIOCORE_LOAD_GAIN") or 0.05)
# 心的追赶时间常数（秒）：越大越迟钝。600s ≈ 十来分钟追上大半。
HEART_TAU_S = float(_os.environ.get("BIOCORE_HEART_TAU_S") or 600.0)
# mood 的时间常数（秒）：文献给的是"小时到天"，取 4 小时。
MOOD_TAU_S = float(_os.environ.get("BIOCORE_MOOD_TAU_S") or 14400.0)
# 与 pipeline 的 LOAD_TAU_S 同源：每拍按 dt/tau 衰减（dt 默认 60s，tau 2000s → 0.03/拍）。
# 显式设 BIOCORE_LOAD_DECAY 时以它为准（旧的按拍 0.002 就等于 0.002/拍）。
LOAD_TAU_S = float(_os.environ.get("BIOCORE_LOAD_TAU_S") or 2000.0)
LOAD_DT_S = float(_os.environ.get("BIOCORE_LOAD_DT_S") or 60.0)
LOAD_DECAY = float(_os.environ.get("BIOCORE_LOAD_DECAY")
                   or min(0.5, LOAD_DT_S / max(1.0, LOAD_TAU_S)))
# 习惯化：同一刺激重复推的衰减系数，以及恢复的时间常数（小时）
HAB_GAIN = float(_os.environ.get("BIOCORE_HAB_GAIN") or 0.6)
HAB_TAU_H = float(_os.environ.get("BIOCORE_HAB_TAU_H") or 6.0)
# how much one idle recollection moves the baseline (small: only volume counts)
REPLAY_LEARN = float(_os.environ.get("BIOCORE_REPLAY_LEARN") or 0.06)
ANCHOR_RELAX = float(_os.environ.get("BIOCORE_ANCHOR_RELAX") or 0.02)

# Values accepted by the parameter loop override the constants above. The loop
# only accepts a change that also improves held-out turns, so this is the seam
# where "learned" reaches the body.
import contextvars as _ctxvars

# 参数作用域：以前是模块级 dict，并发两个会话会互相串。
# 现在优先读 contextvar（请求级），没有才回落到模块级（兼容旧调用）。
_THETA_SCOPE: "_ctxvars.ContextVar" = _ctxvars.ContextVar("biocore_theta", default=None)
_THETA_OVERRIDE = {}


class theta_scope:
    """with theta_scope(values): eff() 在这个块内只读 values。"""

    def __init__(self, values):
        self._values = values
        self._token = None

    def __enter__(self):
        self._token = _THETA_SCOPE.set(self._values or {})
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _THETA_SCOPE.reset(self._token)
        return False


def refresh_theta(store):
    try:
        from biocore_runtime import theta as _th
        _THETA_OVERRIDE.clear()
        _THETA_OVERRIDE.update(_th.values(store))
    except Exception:
        pass
    return dict(_THETA_OVERRIDE)


def eff(key, default):
    try:
        scoped = _THETA_SCOPE.get()
        if isinstance(scoped, dict) and key in scoped:
            return float(scoped[key])
        return float(_THETA_OVERRIDE.get(key, default))
    except Exception:
        return float(default)


# ---- 会话级时间常数 -------------------------------------------------------
# 文献说情绪动力学是个体参数（Kuppens, Oravecz & Tuerlinckx 2010：home base /
# variability / attractor strength 三者都因人而异），所以把 τ 写死一个是错的。
# 取值顺序（高到低）：core_config.json 的 affect 段 > 会话 state.params > 环境默认。
# 保存位置：会话 state.json 的 params 段（见 pipeline 的 affect_params action）。
AFFECT_TAU_BOUNDS = {
    "mood_tau_s": (60.0, 7.0 * 86400.0),      # 1 分钟 .. 7 天
    "heart_tau_s": (10.0, 86400.0),           # 10 秒 .. 1 天
    # 上升时间：情绪从 0 到峰值用多久，与消退分开。0 = 与消退同值（对称，默认）。
    "rise_tau_s": (0.0, 7.0 * 86400.0),
    # 正负消退速度之比：往下走时 tau 乘这个数。>1 = 坏情绪退得更慢。
    "neg_fade_mult": (0.25, 4.0),
}
# 默认 0.67 = 负向退得**更快**，这是 fading affect bias 的文献方向
# （Ritchie et al. 2015, Memory 23(2):278-290: "the negative affect … fading
# faster than the positive affect"；见 docs/AFFECT-LIT.md §5）。
# 1.5（负向退得更慢）是主管给的备选方向，用 BIOCORE_NEG_FADE_MULT=1.5 或
# 会话参数 neg_fade_mult=1.5 打开；两个方向都有实测数字（BUILD-LOG）。
NEG_FADE_MULT = float(_os.environ.get("BIOCORE_NEG_FADE_MULT") or 0.67)
# 上升的判定：目标与当前同号、且离 0 更远，才算"往上走"。
# 文献（AIM 的非线性漂移）说上升与下降不对称，但**没给数值**，
# 所以这里只留机制，默认 0（= 与消退同值，不改变任何既有行为）。


def affect_params(state=None, config=None):
    """把三层的 τ 合成一份数值字典（给 theta_scope 用，也可直接读）。

    session 层读两处：`state["params"][key]` 与顶层的 `state[key]`，
    后者是早期写入的兼容位置（只读，不回写）。
    """
    st = state if isinstance(state, dict) else {}
    sess = st.get("params") if isinstance(st.get("params"), dict) else {}
    cfg = config if isinstance(config, dict) else {}
    affect = cfg.get("affect") if isinstance(cfg.get("affect"), dict) else {}
    out = {}
    for key, default in (("mood_tau_s", MOOD_TAU_S),
                         ("heart_tau_s", HEART_TAU_S),
                         ("rise_tau_s", 0.0),
                         ("neg_fade_mult", NEG_FADE_MULT)):
        value = None
        for cand in (affect.get(key), sess.get(key), st.get(key)):
            if isinstance(cand, (int, float)) and not isinstance(cand, bool):
                value = float(cand)
                break
        if value is None:
            value = float(default)
        lo, hi = AFFECT_TAU_BOUNDS[key]
        out[key] = round(max(lo, min(hi, value)), 3)
    return out


def leak_step(current, target, dt, decay_tau, rise_tau=0.0, neg_mult=1.0):
    """一步指数泄漏；"往上走"用 rise_tau，"往下走"按 neg_mult 放慢。

    往上走 = 目标与当前同号、且 |目标| 比 |当前| 大（离 0 更远）。跨过 0 换号
    算消退：从 +0.4 掉到 -0.2 是"坏下来了"，不是"涨上去了"。
    neg_mult 只管"负向在退"这一件事：当前值在 0 以下、且正在往上（往基线）走时，
    tau 乘 neg_mult。>1 表示坏情绪退得更慢，<1 表示更快；默认 1.0 = 对称，
    行为与只有 decay_tau 时逐位相同。onset（|值| 变大）不归它管，那是 rise_tau。
    """
    try:
        cur = float(current)
        tgt = float(target)
        tau = max(1.0, float(decay_tau))
        rising = abs(tgt) > abs(cur) and (tgt * cur) >= 0.0
        if rising and rise_tau is not None and float(rise_tau) > 0.0:
            tau = max(1.0, float(rise_tau))
        elif cur < 0.0 and tgt > cur and neg_mult and float(neg_mult) != 1.0:
            # 负向在消退（从负值往基线走）：按倍数放慢（或加快）
            tau = max(1.0, tau * float(neg_mult))
        step = 1.0 - math.exp(-max(0.0, float(dt)) / tau)
        return cur + (tgt - cur) * step
    except Exception:
        return float(current)


# ---- 七套情绪系统（Panksepp）的 bias 与 DAN --------------------------------
# activation[k] = clip(w_system[k]·KC + bias[k], 0, 1)；bias 由评价直接给，
# 所以模型关掉、只有数值时这七个数照样动（设计稿 docs/EMOTION-SYSTEMS.md §3）。
SYSTEMS = ("SEEKING", "CARE", "PLAY", "LUST", "FEAR", "RAGE", "PANIC")
LUST_ENABLED = str(_os.environ.get("BIOCORE_LUST") or "0").strip().lower() in (
    "1", "true", "on", "yes")
# PANIC 的双向拐点：0.4–0.7 推开口，>0.7 转退缩（hypervigilance–withdrawal）。
# 方向有文献，0.7 这个数是我们的选择（设计稿 §6）。
PANIC_WITHDRAW_AT = 0.7
PANIC_FLOOR = 0.4


def system_bias(dims=None, state=None, now=None):
    """七套系统的 bias（0..1），全部由已有数值算出，不读字面。"""
    d = dims if isinstance(dims, dict) else {}
    st = state if isinstance(state, dict) else {}
    core = st.get("core") if isinstance(st.get("core"), dict) else {}
    body = st.get("body") if isinstance(st.get("body"), dict) else {}

    def num(*names, default=0.0):
        for src in (d, core, body, st):
            if not isinstance(src, dict):
                continue
            for name in names:
                val = src.get(name)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return float(val)
        return float(default)

    v = max(-1.0, min(1.0, num("valence", "v")))
    a = max(0.0, min(1.0, num("arousal", "a", default=0.5)))
    stress = max(0.0, min(1.0, num("stress")))
    dom = max(-1.0, min(1.0, num("dominance")))
    certainty = max(0.0, min(1.0, num("certainty", default=0.5)))
    novelty = max(0.0, min(1.0, num("novelty")))
    goal = max(-1.0, min(1.0, num("goal")))
    blame = max(0.0, min(1.0, num("blame")))
    hit = max(0.0, min(1.0, num("hit")))
    closeness = max(0.0, min(1.0, num("closeness", default=0.5)))
    social = max(0.0, min(1.0, num("social_drive", "social", default=0.5)))
    # "对方也在痛"：hit 里没有被 blame 指向她的那一部分
    hit_other = max(0.0, hit - blame)
    loneliness = max(0.0, 0.6 - social) * (0.4 + 0.6 * closeness)
    hours_alone = 0.0
    try:
        last = st.get("last_human_ts")
        stamp = float(now) if isinstance(now, (int, float)) else time.time()
        if isinstance(last, (int, float)) and last > 0:
            hours_alone = max(0.0, (stamp - float(last)) / 3600.0)
        else:
            core_ts = st.get("core_state_ts")
            if isinstance(core_ts, (int, float)) and core_ts > 0:
                hours_alone = max(0.0, (stamp - float(core_ts)) / 3600.0)
    except Exception:
        hours_alone = 0.0
    out = {
        "SEEKING": 0.5 * novelty + 0.5 * max(0.0, goal),
        "CARE": 0.6 * closeness + 0.4 * hit_other,
        "PLAY": 0.5 * max(0.0, v) + 0.3 * a + 0.2 * (1.0 - stress),
        "LUST": 0.6 * closeness + 0.4 * max(0.0, v),
        "FEAR": 0.5 * (1.0 - certainty) + 0.3 * stress + 0.2 * blame,
        "RAGE": 0.5 * max(0.0, -v) + 0.3 * max(0.0, dom) + 0.2 * blame,
        "PANIC": 0.6 * loneliness + 0.4 * min(1.0, hours_alone / 12.0),
    }
    if not LUST_ENABLED:
        out["LUST"] = 0.0          # 默认禁用，仅占位
    return {k: round(max(0.0, min(1.0, float(v_))), 4)
            for k, v_ in out.items()}


def system_dan(prev=None, cur=None, dims=None, systems=None):
    """每个系统自己的奖惩分量（-1..1）："它想要的结果有没有发生"。

    只用可观测的数值差分，不读字面；没有上一拍就返回空（不学习）。
    """
    p = prev if isinstance(prev, dict) else {}
    c = cur if isinstance(cur, dict) else {}
    if not p or not c:
        return {}
    d = dims if isinstance(dims, dict) else {}

    def delta(key, default=0.0):
        a = p.get(key, default)
        b = c.get(key, default)
        try:
            return float(b) - float(a)
        except Exception:
            return 0.0

    def clamp(x):
        return max(-1.0, min(1.0, float(x)))

    seek = delta("social_drive") + delta("drive_curiosity")
    care = -delta("stress")
    play = delta("valence")
    neg_p = max(0.0, -float(p.get("valence") or 0.0))
    neg_c = max(0.0, -float(c.get("valence") or 0.0))
    fear = neg_p - neg_c            # 负向减轻 = 躲开了伤害
    rage = delta("dominance") if "dominance" in c else delta("agency")
    panic = delta("social_drive")   # 有人回应 = 想要的接触发生了
    out = {"SEEKING": clamp(seek * 2.0), "CARE": clamp(care * 2.0),
           "PLAY": clamp(play * 2.0), "LUST": 0.0,
           "FEAR": clamp(fear * 2.0), "RAGE": clamp(rage * 2.0),
           "PANIC": clamp(panic * 2.0)}
    return {k: round(v, 4) for k, v in out.items() if abs(v) > 1e-6}


def panic_effective(value):
    """PANIC 的"推开口"有效量：0.4–0.7 上升，>0.7 转负（退缩）。

    没有这一条，"越孤独越黏"就是假人（设计稿 §4）。
    """
    try:
        x = max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0
    if x <= PANIC_FLOOR:
        return 0.0
    if x <= PANIC_WITHDRAW_AT:
        # 0.4 → 0，0.7 → 1：这一段是"想找人"
        return (x - PANIC_FLOOR) / (PANIC_WITHDRAW_AT - PANIC_FLOOR)
    # 过了拐点：从 +1 连续往下走，0.82 附近过零，1.0 触底 −1。
    # 连续（不在 0.7 处跳变），方向按 hypervigilance–withdrawal。
    return max(-1.0, 1.0 - 2.5 * (x - PANIC_WITHDRAW_AT)
               / max(1e-6, 1.0 - PANIC_WITHDRAW_AT))


def _body_path(store, session_id):
    import os
    import re
    sid = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "s1"))[:60]
    return os.path.join(str(store), "sessions", sid, BODY_STATE_FILE)


def load_state(store, session_id) -> Dict[str, float]:
    """Read the last persisted body state for one session (may be empty)."""
    import json
    import os
    path = _body_path(store, session_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        out = {}
        for key, value in data.items():
            if isinstance(value, (int, float)):
                out[key] = float(value)
            elif key == "action" and isinstance(value, str):
                # the body's decision is a string; dropping it here made every
                # restored turn look like it wanted to speak
                out[key] = value
        return out
    except Exception:
        return {}


def save_body_state(store, session_id, state) -> bool:
    """存身体状态时，把"身体不产"的数字键保留下来。

    body.state() 只报身体自己的字段；silent_beats、sys_* 这些由别的层写进去
    的键会被整份覆盖，于是"沉默越久越可能开口"的重尾永远在 0 上求值。
    只在七系统活着（存量里有 sys_*）时保留，保证关掉开关时与改动前一致。
    """
    try:
        prev = load_state(store, session_id)
        _keep = any(str(k).startswith("sys_") for k in prev)
        if isinstance(state, dict) and _keep:
            for key, value in prev.items():
                if key not in state and isinstance(value, (int, float)):
                    state[key] = value
    except Exception:
        pass
    return save_state(store, session_id, state)


def save_state(store, session_id, state: Dict[str, float]) -> bool:
    import json
    import os
    path = _body_path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def body_for(store, session_id, seed: int = 1) -> "DialogueBody":
    """A body resumed from disk: the process dies every turn, the state does not."""
    body = DialogueBody(seed=seed)
    prev = load_state(store, session_id)
    if prev:
        try:
            body.anchor["valence"] = float(prev.get("anchor_valence") or 0.0)
            body.anchor["stress"] = float(prev.get("anchor_stress") or 0.0)
            # 慢层要跨轮活着，否则每轮都从 0 开始，等于没有 mood 与 load
            body.slow["mood"] = float(prev.get("mood") or 0.0)
            body.slow["load"] = float(prev.get("allostatic_load") or 0.0)
            # 心也要跨轮活着：600 秒的低通如果每轮从 0 重新开始，它每轮只走
            # 一步（9.5%），"时间连续"就名存实亡——实测心一路贴着 0，而原始
            # 读数在 -0.7 的时候它报 -0.01。
            if prev.get("heart") is not None:
                body.slow["heart"] = float(prev.get("heart") or 0.0)
            # 七套系统也跨轮活着（扁平键 sys_<NAME>）
            _sys = {name: float(prev["sys_" + name])
                    for name in SYSTEMS
                    if isinstance(prev.get("sys_" + name), (int, float))}
            if _sys:
                body.slow["systems"] = _sys
            body.commit["open"] = float(prev.get("commit_open") or 0.0)
            body.commit["kept"] = float(prev.get("commit_kept") or 0.0)
            try:
                dv = body.mind.drives.values
                for key, field in (("drive_safety", "safety"),
                                   ("drive_energy", "energy"),
                                   ("drive_curiosity", "curiosity"),
                                   ("social_drive", "social")):
                    if key in prev:
                        dv[field] = max(0.0, min(1.0, float(prev[key])))
            except Exception:
                pass
        except Exception:
            pass
        try:
            body.mind.inject_affect(prev.get("valence", 0.0),
                                    prev.get("arousal", 0.5),
                                    prev.get("dominance", 0.0),
                                    intensity=1.0, blend=1.0)
            # 生命循环要求身体跨轮连续：stress / agency 此前每轮被重置，
            # 导致"过阈"永远看不到上一轮留下的压力。只恢复已存在的数字，
            # 不造新值（没有该键就保持默认）。
            try:
                if prev.get("stress") is not None:
                    body.mind.affect.stress = max(
                        0.0, min(1.0, float(prev.get("stress") or 0.0)))
                if prev.get("agency") is not None:
                    body.mind.self_model.agency = max(
                        0.0, min(1.0, float(prev.get("agency") or 0.0)))
            except Exception:
                pass
        except Exception:
            pass
    try:
        replay_mirror(body, store, session_id)
        restore_mb(body, store, session_id)
    except Exception:
        pass
    return body


def save_digest(store, session_id, lines) -> bool:
    """Remember what she just recalled, so it can colour the next turn.

    A separate model call for recalled text would double the per-turn cost.
    Instead the digest rides along with the next turn's own appraisal, which
    is also truer to how memory works: something surfaces, then colours what
    comes after it.
    """
    import os
    path = _body_path(store, session_id).replace(BODY_STATE_FILE, RECALL_DIGEST_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(str(x) for x in (lines or []))[:600])
        return True
    except Exception:
        return False


def load_digest(store, session_id) -> str:
    import os
    path = _body_path(store, session_id).replace(BODY_STATE_FILE, RECALL_DIGEST_FILE)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def turn(store, session_id, text: str, dims: Optional[Dict[str, float]] = None,
         seed: int = 1, with_memory: bool = True, outs=None) -> Dict[str, float]:
    """One user turn: resume, feed, persist, return the new state."""
    body = body_for(store, session_id, seed=seed)
    state = body.feed(text, dims, outs=outs)
    if with_memory:
        digest = load_digest(store, session_id)
        if digest:
            # what she recalled last turn is still in the room
            state = body.feed(digest, dims, ticks=0)
    save_state(store, session_id, state)
    return state


def state_line(state: Dict[str, float]) -> str:
    """One line of body state for the prompt. Quantities only, no advice."""
    if not state:
        return ""
    v = state.get("valence", 0.0)
    s = state.get("stress", 0.0)
    a = state.get("arousal", 0.0)
    mood = "低落" if v <= -0.15 else ("轻快" if v >= 0.15 else "平稳")
    tension = "紧绷" if s >= 0.25 else ("松弛" if s <= 0.08 else "适中")
    return "【身体】情绪%s（%.2f）· 紧张度%s（%.2f）· 唤醒%.2f" % (mood, v, tension, s, a)


MIRROR_FILE = "mindbase_mirror.json"


def _mirror_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, MIRROR_FILE)


def load_mirror(store, session_id):
    """Remembered lines mirrored from the main memory (survives the process)."""
    import json
    try:
        with open(_mirror_path(store, session_id), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_mirror(store, session_id, rows, cap: int = 200):
    import json
    import os
    rows = list(rows or [])[-int(cap):]
    path = _mirror_path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
        return True
    except Exception:
        return False


def mirror_recall(store, session_id, lines, valence: float = 0.0):
    """Copy recalled lines into the substrate's own memory.

    The two memories were separate systems that never exchanged anything. The
    substrate memory is not persisted (no restore path), so the mirrored rows
    are kept here and replayed into each fresh body.
    """
    import time as _t
    _t_now = _t.time()
    rows = load_mirror(store, session_id)
    seen = {str(r.get("text") or "") for r in rows}
    for line in lines or []:
        text = str(line or "").lstrip("- ").strip()
        if not text or text in seen:
            continue
        rows.append({"text": text[:400], "valence": float(valence or 0.0),
                     "ts": float(_t_now)})
        seen.add(text)
    save_mirror(store, session_id, rows)
    return len(rows)


def replay_mirror(body, store, session_id, now: float = 0.0) -> int:
    """Write mirrored rows back into a fresh body's memory."""
    rows = load_mirror(store, session_id)
    written = 0
    mem = getattr(body.mind, "memory", None)
    if mem is None or not rows:
        return 0
    for row in rows:
        try:
            mem.write(str(row.get("text") or ""), importance=0.5, rpe=0.1,
                      now=now or float(body.mind.t),
                      valence=float(row.get("valence") or 0.0))
            written += 1
        except Exception:
            continue
    return written


NEEDS_FILE = "mindbase_needs.json"


def _needs_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, NEEDS_FILE)


def higher_needs(state, history=None):
    """L3-L5: the needs the substrate's fitness never measured.

    Survival is a gate, not a goal: every organism clears it, so it cannot rank
    anything. These three are what separate alive from living well.
    """
    v = float((state or {}).get("valence") or 0.0)
    s = float((state or {}).get("stress") or 0.0)
    hist = list(history or [])[-12:]
    vals = [float(h.get("valence") or 0.0) for h in hist] or [v]
    mean_v = sum(vals) / len(vals)
    spread = (sum((x - mean_v) ** 2 for x in vals) / len(vals)) ** 0.5
    competence = max(0.0, min(1.0, 0.5 + 0.4 * mean_v - 0.5 * s - 0.3 * spread))
    speaks = sum(1 for h in hist if str(h.get("action") or "") == "speak")
    silences = sum(1 for h in hist if str(h.get("action") or "") == "silence")
    total = max(1, speaks + silences)
    balance = 1.0 - abs(speaks / total - 0.65)
    autonomy = max(0.0, min(1.0, 0.5 * balance + 0.5 * (1.0 - abs(v))))
    opened = float((state or {}).get("commit_open") or 0.0)
    kept = float((state or {}).get("commit_kept") or 0.0)
    # An outstanding promise is not a broken one: counting it as zero punished
    # her for agreeing to anything. Half credit while it is still pending.
    total_c = opened + kept
    meaning = ((kept + 0.5 * opened) / total_c) if total_c > 0 else 0.5
    return {"competence": round(competence, 4), "autonomy": round(autonomy, 4),
            "meaning": round(meaning, 4)}


def fitness_of(state, history=None, survival=1.0):
    """Survival is a gate; the higher needs decide the ordering."""
    if survival < 0.5:
        return 0.0
    n = higher_needs(state, history)
    return round(0.45 * survival + 0.20 * n["competence"]
                 + 0.20 * n["autonomy"] + 0.15 * n["meaning"], 4)


def load_history(store, session_id):
    import json
    try:
        with open(_needs_path(store, session_id), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_history(store, session_id, data):
    import json
    import os
    path = _needs_path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return True
    except Exception:
        return False


def micro_evolve(store, session_id, state=None, step=0.03):
    """One tiny hill-climb per turn: continuous iteration, no batch run.

    At a few turns a day a real search never collects enough samples, but a
    hill-climb on one bounded knob still improves with one observation per turn.
    """
    hist = load_history(store, session_id)
    rows = list(hist.get("turns") or [])[-24:]
    if state:
        rows.append({"valence": float(state.get("valence") or 0.0),
                     "stress": float(state.get("stress") or 0.0),
                     "action": str(state.get("action") or "")})
    hist["turns"] = rows[-24:]
    fit = fitness_of(state or {}, rows)
    prev = float(hist.get("fitness") or 0.0)
    knob = float(hist.get("noise_amp") or BODY_NOISE_AMP)
    if rows and fit >= prev:
        knob = min(0.6, knob * (1.0 + step))
    else:
        knob = max(0.05, knob * (1.0 - step))
    hist["fitness"] = fit
    hist["noise_amp"] = round(knob, 4)
    hist["needs"] = higher_needs(state or {}, rows)
    save_history(store, session_id, hist)
    out = {"fitness": fit, "noise_amp": round(knob, 4)}
    out.update(hist["needs"])
    return out


def judge_exchange(user_text, reply_text, config=None, timeout=60.0):
    """The brain grades the exchange; the body only consumes the number.

    Reward was hand-written (did the human reply? how fast?). That puts the
    judgement in the harness instead of the model. Here the model reads both
    sides and returns how well the reply met the moment; the biological layer
    never invents its own meaning of success.
    """
    import json as _json
    import re as _re
    import urllib.request as _url
    cfg = dict(config or {})
    base = str(cfg.get("base_url") or "").rstrip("/")
    model = str(cfg.get("model") or "")
    key = str(cfg.get("key") or cfg.get("api_key") or "")
    if not (base and model and key):
        return {}
    prompt = prompt_text("judge")
    body = _json.dumps({"model": model, "temperature": 0,
                        "messages": [{"role": "system", "content": prompt},
                                     {"role": "user",
                                      "content": "对方说：" + str(user_text or "")[:600]
                                      + chr(10) + "她回：" + str(reply_text or "")[:900]}]}).encode()
    req = _url.Request(base + "/chat/completions", data=body,
                       headers={"Authorization": "Bearer " + key,
                                "Content-Type": "application/json"})
    try:
        with _url.urlopen(req, timeout=timeout) as resp:
            raw = _json.loads(resp.read().decode())["choices"][0]["message"]["content"]
        m = _re.search(r"\{.*\}", raw, _re.S)
        data = _json.loads(m.group(0)) if m else {}
    except Exception:
        return {}
    out = {}
    for name in ("reward", "fit"):
        if name in data:
            try:
                out[name] = max(-1.0, min(1.0, float(data[name])))
            except Exception:
                continue
    if "note" in data:
        out["note"] = str(data["note"])[:24]
    return out


MB_FILE = "mindbase_mb.json"


_PROMPTS = None


def prompt_text(name):
    """Prompt text lives in data/prompts.json: the core contract forbids CJK
    string literals in code, and prompts are data, not logic.

    data/prompts.json 是可部署、可被宿主覆盖的那份（在 .gitignore 里，不进仓）；
    它缺某个键时退回同目录下**入库**的 prompts_default.json。
    逐键合并、前者优先，所以新增一个键（appraise_dims）不需要改部署产物。
    """
    global _PROMPTS
    if _PROMPTS is None:
        import json as _json
        import os as _os2
        here = _os2.path.dirname(_os2.path.abspath(__file__))
        merged = {}
        for path in (_os2.path.join(here, "data", "prompts.json"),
                     _os2.path.join(here, "prompts_default.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    doc = _json.load(fh)
            except Exception:
                continue
            if isinstance(doc, dict):
                for key, value in doc.items():
                    merged.setdefault(str(key), value)
        _PROMPTS = merged
    return str(_PROMPTS.get(str(name)) or "")


def _mb_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, MB_FILE)


def load_mb(store, session_id):
    import json
    try:
        with open(_mb_path(store, session_id), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_mb(store, session_id, data):
    import json
    import os
    path = _mb_path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return True
    except Exception:
        return False


def teach(store, session_id, cue, reward):
    """One teaching trial: DAN writes into the KC->MBON weights.

    This is the layer below the policy table. The policy learns *whether* to
    reach out; the mushroom body learns *which cue* predicts what. Both now
    consume the same number, graded by the model.
    """
    if _IMPORT_ERROR:
        return {}
    body = body_for(store, session_id)
    mb = getattr(body.mind, "mb", None)
    if mb is None:
        return {}
    try:
        out = mb.learn(str(cue or ""), float(reward or 0.0))
    except Exception as exc:
        return {"error": type(exc).__name__}
    try:
        save_mb(store, session_id, {
            "reward": list(getattr(mb.mbon_reward, "w", [])),
            "punish": list(getattr(mb.mbon_punishment, "w", [])),
        })
    except Exception:
        pass
    out = dict(out or {})
    out["cue"] = str(cue or "")[:40]
    return out


def restore_mb(body, store, session_id):
    data = load_mb(store, session_id)
    if not data:
        return 0
    mb = getattr(body.mind, "mb", None)
    if mb is None:
        return 0
    n = 0
    for key, target in (("reward", getattr(mb, "mbon_reward", None)),
                        ("punish", getattr(mb, "mbon_punishment", None))):
        vec = data.get(key)
        if not isinstance(vec, list) or target is None:
            continue
        try:
            if len(vec) == len(target.w):
                target.w = [float(x) for x in vec]
                n += 1
        except Exception:
            continue
    return n


CLOCK_FILE = "mindbase_clock.json"
PREV_FILE = "mindbase_prev.json"
LEARN_FILE = "learn_log.jsonl"
LEARN_CAP = 5000


def log_learn(store, session_id, dims, outs, before, after):
    """一行一条：评价值 → 八个输出 → 状态怎么变。

    这是拟合评价矩阵唯一需要的数据，没有它 49 个权重只能继续手设。
    """
    import json
    import os
    if not isinstance(dims, dict) or not dims:
        return False
    row = {
        "dims": {k: round(float(dims.get(k) or 0.0), 4) for k in
                 ("goal", "blame", "control", "certainty", "novelty", "involve", "hit")},
        "outs": {k: round(float(v), 4) for k, v in (outs or {}).items()
                 if isinstance(v, (int, float))},
        "before": {k: round(float((before or {}).get(k) or 0.0), 4)
                   for k in ("valence", "arousal", "stress", "social_drive")},
        "after": {k: round(float((after or {}).get(k) or 0.0), 4)
                  for k in ("valence", "arousal", "stress", "social_drive")},
    }
    try:
        path = _body_path(store, session_id).replace(BODY_STATE_FILE, LEARN_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            except Exception:
                lines = []
        lines.append(json.dumps(row, ensure_ascii=False))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines[-LEARN_CAP:]) + "\n")
        return True
    except Exception:
        return False


def save_prev(store, session_id, state):
    """Snapshot before this turn touches anything: the KL baseline."""
    import json
    import os
    try:
        path = _body_path(store, session_id).replace(BODY_STATE_FILE, PREV_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({k: float(v) for k, v in (state or {}).items()
                       if isinstance(v, (int, float))}, fh)
        return True
    except Exception:
        return False


def load_prev(store, session_id):
    import json
    try:
        with open(_body_path(store, session_id).replace(BODY_STATE_FILE, PREV_FILE),
                  "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


# PI 控制器参数：P 沿用原来的回拉强度，I 小而慢，只负责消掉稳态误差
PI_KP = float(_os.environ.get("BIOCORE_PI_KP") or 0.04)
PI_KI = float(_os.environ.get("BIOCORE_PI_KI") or 0.010)
PI_KI_DT = float(_os.environ.get("BIOCORE_PI_DT") or 1.0)
PI_I_LIMIT = float(_os.environ.get("BIOCORE_PI_LIMIT") or 0.6)
ANCHOR_GAIN = PI_KP

NORM_FILE = "mindbase_norm.json"
NORM_ALPHA = 0.05     # how fast the personal baseline follows her
NORM_MIN = 8          # samples before z is trustworthy


def _norm_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, NORM_FILE)


def norm_update(store, session_id, state, alpha=None):
    """Running mean/std per dimension, then her position in her own units.

    Absolute numbers are not readable: "-0.15" means nothing without knowing
    where she usually sits. z = (x - mean) / std is what a model can read.
    """
    import json
    import math
    import os
    a = float(alpha if alpha is not None else NORM_ALPHA)
    keys = ("valence", "arousal", "stress", "social_drive")
    try:
        with open(_norm_path(store, session_id), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        doc = {}
    rows = doc.get("rows") or {}
    out = {}
    for key in keys:
        val = (state or {}).get(key)
        if not isinstance(val, (int, float)):
            continue
        cur = rows.get(key) or {"n": 0, "mean": 0.0, "var": 0.04}
        n = int(cur.get("n") or 0)
        mean = float(cur.get("mean") or 0.0)
        var = float(cur.get("var") or 0.04)
        if n < 1:
            mean, var = float(val), 0.04
        else:
            delta = float(val) - mean
            mean = mean + a * delta
            var = (1 - a) * var + a * delta * delta
        if n >= NORM_MIN:
            sd = max(1e-3, math.sqrt(max(var, 1e-6)))
            out[key] = round((float(val) - mean) / sd, 2)
        else:
            out[key] = 0.0
        rows[key] = {"n": n + 1, "mean": round(mean, 5), "var": round(var, 5)}
    try:
        os.makedirs(os.path.dirname(_norm_path(store, session_id)), exist_ok=True)
        with open(_norm_path(store, session_id), "w", encoding="utf-8") as fh:
            json.dump({"rows": rows, "alpha": a}, fh)
    except Exception:
        pass
    return out


KL_SIGMA = {"valence": 0.25, "arousal": 0.15, "dominance": 0.30,
            "stress": 0.20, "social_drive": 0.25}


def state_kl(before, after):
    """How far one exchange moved the state, in nats (diagonal Gaussian KL).

    A number, not a verdict: it says "this landed" -- not "this was good".
    """
    import math
    total = 0.0
    for key, sigma in KL_SIGMA.items():
        a = (before or {}).get(key)
        c = (after or {}).get(key)
        if not isinstance(a, (int, float)) or not isinstance(c, (int, float)):
            continue
        total += ((float(c) - float(a)) ** 2) / (2.0 * sigma * sigma)
    return round(total, 5)
TICK_SECONDS = 60.0        # one body tick per simulated minute
MAX_CATCHUP_TICKS = 480    # 8 hours: beyond that the gap is a "night", not a gap


def _clock_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, CLOCK_FILE)


def _clock_load(store, session_id):
    import json
    try:
        with open(_clock_path(store, session_id), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _clock_save(store, session_id, ts):
    import json
    import os
    try:
        path = _clock_path(store, session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"last_tick": float(ts)}, fh)
        return True
    except Exception:
        return False


HEARTBEAT_FILE = "mindbase_heartbeat.jsonl"
HEARTBEAT_CAP = 400


def heartbeat_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, HEARTBEAT_FILE)


def heartbeat_last(store, session_id):
    """Last heartbeat row (corrupt/missing file -> {}; never raises)."""
    import json
    path = heartbeat_path(store, session_id)
    try:
        rows = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(line)
        for line in reversed(rows):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                return row
    except Exception:
        pass
    return {}


def _gloss_path(store, session_id):
    if not store or not session_id:
        return ""
    return _body_path(store, session_id).replace(BODY_STATE_FILE, "sys_gloss.json")


def _snapshot_path(store, session_id):
    if not store or not session_id:
        return ""
    return _body_path(store, session_id).replace(BODY_STATE_FILE, "state_snapshot.json")


def snapshot_get(store, session_id):
    """上一次已经报给模型的状态（只报变动时用来对比）。"""
    import json
    path = _snapshot_path(store, session_id)
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def snapshot_set(store, session_id, snap):
    import json
    import os
    path = _snapshot_path(store, session_id)
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snap or {}, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def gloss_get(store, session_id):
    """上一次报给模型的"心里状态"（词集）。单独成文件，避免被身体状态整份覆盖。"""
    import json
    path = _gloss_path(store, session_id)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return str((json.load(fh) or {}).get("gloss") or "")
    except Exception:
        return ""


def gloss_set(store, session_id, gloss):
    import json
    import os
    path = _gloss_path(store, session_id)
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"gloss": str(gloss or "")}, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def wish_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, "mindbase_wishes.jsonl")


def wish_stat_append(store, session_id, row, cap=2000):
    """"想说话但没发出去"的计数流水，与心跳分开存，便于单独统计。"""
    import json
    import os
    path = wish_path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + chr(10))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) > int(cap):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-int(cap):])
        except Exception:
            pass
        return True
    except Exception:
        return False


def wish_last(store, session_id):
    """读"想说话"流水的最后一行（计数以流水为准，不依赖 state 的覆盖行为）。"""
    import json
    import os
    path = wish_path(store, session_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
        return json.loads(lines[-1]) if lines else {}
    except Exception:
        return {}


def heartbeat_append(store, session_id, row, cap=HEARTBEAT_CAP):
    """Append one observability line outside state.json (safe if file corrupt)."""
    import json
    import os
    path = heartbeat_path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(row, ensure_ascii=False) + chr(10)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) > int(cap):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-int(cap):])
        except Exception:
            pass
        return True
    except Exception:
        return False


def clock_value(store, session_id):
    """Last advance() clock (the same one advance() uses for elapsed)."""
    try:
        return float(_clock_load(store, session_id).get("last_tick") or 0.0)
    except Exception:
        return 0.0


def advance(store, session_id, now=None, seed=1):
    """Let time pass while nobody is talking.

    Until now the body only moved when a message arrived, so she had no life
    between conversations: no drift, no settling, no waking up different.
    Ticks are capped and batched -- eight hours is a night, not four hundred
    separate minutes of noise.
    """
    if _IMPORT_ERROR:
        return {}
    import time as _t
    now = float(now if now is not None else _t.time())
    clock = _clock_load(store, session_id)
    last = float(clock.get("last_tick") or 0.0)
    if last <= 0:
        _clock_save(store, session_id, now)
        return {"ticks": 0, "seconds": 0.0}
    elapsed = max(0.0, now - last)
    ticks = int(elapsed // TICK_SECONDS)
    if ticks <= 0:
        return {"ticks": 0, "seconds": round(elapsed, 1)}
    ticks = min(ticks, MAX_CATCHUP_TICKS)
    body = body_for(store, session_id, seed=seed)
    # batched: each tick carries the elapsed share so the noise integrates once
    dt = max(1.0, elapsed / max(1, ticks))
    for _ in range(ticks):
        body._tick_once(dt=min(dt, 600.0))
    state = body.state()
    save_body_state(store, session_id, state)
    _clock_save(store, session_id, now)
    return {"ticks": ticks, "seconds": round(elapsed, 1), "state": state}


IDLE_FILE = "mindbase_idle.json"


def _idle_path(store, session_id):
    return _body_path(store, session_id).replace(BODY_STATE_FILE, IDLE_FILE)


def idle_resume_point(store, session_id):
    """上次回放到的时刻；没有就返回 None（表示还没跑过）。"""
    hist = load_idle(store, session_id)
    state = hist.get("run_state") if isinstance(hist.get("run_state"), dict) else {}
    try:
        last = float(state.get("last_ts") or 0.0)
        return last if last > 0 else None
    except Exception:
        return None


def load_idle(store, session_id):
    import json
    try:
        with open(_idle_path(store, session_id), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_idle(store, session_id, data):
    import json
    import os
    path = _idle_path(store, session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        return True
    except Exception:
        return False


def idle_activity(store, session_id, now=None, seed=1, rng=None):
    """What she does while nobody is talking.

    Two kinds of inner events, both cheap and both recorded, because "alive"
    without evidence is only state drift:

      replay  pick a mirrored memory and run it through the body again
      muse    a self-generated note: what she would say, not said

    Volume is the point: a few events per idle hour accumulate into patterns
    that a single turn never produces.
    """
    import random as _random
    import time as _t
    if _IMPORT_ERROR:
        return {}
    rng = rng or _random.Random(int(seed) ^ int((now or _t.time()) // 60))
    hist = load_idle(store, session_id)
    rows = load_mirror(store, session_id)
    body = body_for(store, session_id, seed=seed)

    # every idle run lets the baseline drift back a little: without this, a
    # stretch of one-sided memories saturates the anchor (-0.94 after 48 runs)
    body.anchor["valence"] = round(
        body.anchor["valence"] * (1.0 - ANCHOR_RELAX), 4)
    body.anchor["stress"] = round(
        body.anchor["stress"] * (1.0 - ANCHOR_RELAX), 4)
    events = []
    if rows:
        def _weight(r):
            # recency: recent memories replay first (reverse replay);
            # salience: stronger affect replays more often
            age_h = max(0.0, (float(now or 0) - float(r.get("ts") or 0.0)) / 3600.0)
            rec = 2.718 ** (-age_h / 24.0)
            sal = 0.5 + abs(float(r.get("valence") or 0.0))
            return max(1e-6, rec * sal)
        for _ in range(min(3, max(1, len(rows) // 4))):
            total = sum(_weight(r) for r in rows)
            pick = rng.random() * total
            acc = 0.0
            row = rows[0]
            for cand in rows:
                acc += _weight(cand)
                if acc >= pick:
                    row = cand
                    break
            text = str(row.get("text") or "")
            val = float(row.get("valence") or 0.0)
            # replaying a memory pushes the body the way it did when it happened
            # half strength: a recollection is not a fresh blow, otherwise a
            # run of negative memories pins valence at its floor (-0.94)
            body.feed(text, {"valence": val * 0.5, "arousal": 0.45}, ticks=0)
            # a replayed memory keeps colouring her afterwards: volume is what
            # turns a single recollection into a baseline shift
            body.anchor["valence"] = round(
                body.anchor["valence"] * (1 - eff("replay_learn", REPLAY_LEARN))
                + val * eff("replay_learn", REPLAY_LEARN), 4)
            events.append({"kind": "replay", "text": text[:40], "valence": val})

    state = body.state()
    v = float(state.get("valence") or 0.0)
    stress = float(state.get("stress") or 0.0)
    if rng.random() < 0.5:
        note = "不想说话" if (stress > 0.25 or v < -0.2) else "想说点什么"
        events.append({"kind": "muse", "text": note,
                       "valence": round(v, 3), "stress": round(stress, 3)})

    save_body_state(store, session_id, state)
    hist["replays"] = int(hist.get("replays") or 0) + sum(1 for e in events if e["kind"] == "replay")
    hist["muses"] = int(hist.get("muses") or 0) + sum(1 for e in events if e["kind"] == "muse")
    hist["runs"] = int(hist.get("runs") or 0) + 1
    # 18 恢复即产物：把「回放到哪了」也落盘，进程被杀后能接着跑而不是从头
    hist["run_state"] = {"last_ts": round(float(now or 0.0), 1),
                         "replays": int(hist.get("replays") or 0),
                         "muses": int(hist.get("muses") or 0)}
    hist["last"] = {"ts": round(float(now or _t.time()), 1),
                    "events": events[-6:], "state": state}
    save_idle(store, session_id, hist)
    return {"runs": hist["runs"], "replays": hist["replays"],
            "muses": hist["muses"], "events": events, "state": state}
