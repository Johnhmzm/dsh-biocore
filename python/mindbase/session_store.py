from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

from .memory import MemoryItem
from .social import Bond
import numpy as np
from .fly_mb import FlyMushroomBody


class SessionStore:
    """Persist per-user session: facts, turns, identity, drives, affect."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        key = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        return self.root / (key + ".json")

    def _fly_path(self, user_id: str) -> Path:
        key = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        return self.root / (key + ".fly.npz")

    def save(self, user_id: str, session) -> None:
        try:
            m = session.mind
            facts = []
            for item in list(m.memory.semantic.items)[-200:]:
                facts.append({
                    "text": item.text,
                    "importance": float(item.importance),
                    "t": float(item.t),
                    "confidence": float(getattr(item, "confidence", 0.5)),
                })
            bond = m.social.bonds.get(str(user_id))
            social = {
                "trust": float(bond.trust) if bond else 0.5,
                "attachment": float(bond.attachment) if bond else 0.1,
                "familiarity": float(bond.familiarity) if bond else 0.1,
                "responsiveness": float(bond.responsiveness) if bond else 0.5,
                "stage": str(getattr(bond, "stage", "stranger")) if bond else "stranger",
            } if bond else {}
            episodic = []
            for item in list(m.memory.episodic.items)[-200:]:
                episodic.append({"text": item.text, "importance": float(item.importance), "t": float(item.t), "confidence": float(getattr(item, "confidence", 0.5))})
            evolution = None
            try:
                for exp in getattr(session, "kernel", None).experiments:
                    if getattr(exp, "name", "") == "evolution" and hasattr(exp, "state"):
                        evolution = exp.state()
                        break
            except Exception:
                evolution = None
            data = {
                "user_id": str(user_id),
                "evolution": evolution,
                "social": social,
                "episodic": episodic,
                "t": float(m.t),
                "identity": float(m.self_model.identity),
                "drives": {k: float(v) for k, v in m.drives.values.items()},
                "affect": {
                    "v": float(getattr(m.affect, "v", 0.0)),
                    "a": float(getattr(m.affect, "a", 0.0)),
                    "stress": float(getattr(m.affect, "stress", 0.0)),
                },
                "facts": facts,
                "emotion": session.emotion.snapshot() if hasattr(session, "emotion") else None,
                "turns": list(session.turns)[-30:],
            }
            self._path(user_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            fly = getattr(getattr(session.mind, "circuit", None), "fly_mb", None)
            if fly is not None:
                np.savez_compressed(self._fly_path(user_id), w_approach=fly.w_approach, w_avoid=fly.w_avoid, trace=fly.trace,
                                    sparsity=np.array([fly.sparsity]), lr=np.array([fly.lr]), apl_gain=np.array([fly.apl_gain]),
                                    trace_decay=np.array([fly.trace_decay]), pam_lr=np.array([fly.pam_lr]), ppl1_lr=np.array([fly.ppl1_lr]),
                                    reward_expectation=np.array([fly.reward_expectation]), punishment_expectation=np.array([fly.punishment_expectation]),
                                    pam_gamma_ratio=np.array([fly.pam_gamma_ratio]), ppl1_alpha_ratio=np.array([fly.ppl1_alpha_ratio]),
                                    weight_decay=np.array([fly.weight_decay]),
                                    gamma=np.array([fly.subregion_gain["gamma"]]), alpha=np.array([fly.subregion_gain["alpha"]]), beta=np.array([fly.subregion_gain["beta"]]),
                                    age_steps=np.array([fly.age_steps]), plasticity=np.array([fly.plasticity]))
        except Exception:
            pass

    def _load_fly(self, user_id: str, session) -> None:
        fly_path = self._fly_path(user_id)
        if not fly_path.exists():
            return
        try:
            m = session.mind
            fly = getattr(getattr(m, "circuit", None), "fly_mb", None)
            if fly is None:
                fly = FlyMushroomBody(seed=1)
                m.circuit.fly_mb = fly
            z = np.load(fly_path)
            fly.w_approach = z["w_approach"]
            fly.w_avoid = z["w_avoid"]
            fly.trace = z["trace"]
            fly.sparsity = float(z["sparsity"][0])
            fly.lr = float(z["lr"][0])
            fly.apl_gain = float(z["apl_gain"][0])
            fly.trace_decay = float(z["trace_decay"][0])
            fly.pam_lr = float(z["pam_lr"][0])
            fly.ppl1_lr = float(z["ppl1_lr"][0])
            fly.reward_expectation = float(z["reward_expectation"][0])
            fly.punishment_expectation = float(z["punishment_expectation"][0])
            fly.pam_gamma_ratio = float(z["pam_gamma_ratio"][0])
            fly.ppl1_alpha_ratio = float(z["ppl1_alpha_ratio"][0])
            fly.weight_decay = float(z["weight_decay"][0])
            fly.subregion_gain = {"gamma": float(z["gamma"][0]), "alpha": float(z["alpha"][0]), "beta": float(z["beta"][0])}
        except Exception:
            pass

    def load(self, user_id: str, session) -> bool:
        path = self._path(user_id)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            m = session.mind
            m.t = float(data.get("t", m.t))
            m.self_model.identity = float(data.get("identity", m.self_model.identity))
            for key, value in (data.get("drives") or {}).items():
                if key in m.drives.values:
                    m.drives.values[key] = float(value)
            aff = data.get("affect") or {}
            m.affect.v = float(aff.get("v", m.affect.v))
            m.affect.a = float(aff.get("a", m.affect.a))
            m.affect.stress = float(aff.get("stress", m.affect.stress))
            soc = data.get("social") or {}
            if soc:
                bond = m.social.bonds.setdefault(str(user_id), Bond())
                bond.trust = float(soc.get("trust", bond.trust))
                bond.attachment = float(soc.get("attachment", bond.attachment))
                bond.familiarity = float(soc.get("familiarity", bond.familiarity))
                bond.responsiveness = float(soc.get("responsiveness", bond.responsiveness))
                bond.stage = str(soc.get("stage", bond.stage))
            for item in data.get("episodic") or []:
                m.memory.episodic.items.append(MemoryItem(
                    text=str(item.get("text") or ""),
                    importance=float(item.get("importance", 0.5)),
                    t=float(item.get("t", 0.0)),
                    confidence=float(item.get("confidence", 0.5)),
                ))
            for item in data.get("facts") or []:
                m.memory.semantic.items.append(MemoryItem(
                    text=str(item.get("text") or ""),
                    importance=float(item.get("importance", 0.5)),
                    t=float(item.get("t", 0.0)),
                    confidence=float(item.get("confidence", 0.5)),
                ))
            session.turns = list(data.get("turns") or [])
            try:
                if hasattr(session, "emotion") and data.get("emotion"):
                    session.emotion.load(data.get("emotion"))
            except Exception:
                pass
            evo = data.get("evolution")
            if evo:
                try:
                    for exp in getattr(session, "kernel", None).experiments:
                        if getattr(exp, "name", "") == "evolution" and hasattr(exp, "load_state"):
                            exp.load_state(evo)
                            break
                except Exception:
                    pass
            self._load_fly(user_id, session)
            return True
        except Exception:
            return False
