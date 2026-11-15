from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .appraisal import AppraisalModel, CompositeAppraisalModel
from .timing import TimingModel
from .general import GeneralPolicyModel
from .linear import LinearRegressor, RidgeRegressor, SoftmaxRegression
from .policy import PolicyModel
from .world_model import WorldModel


def save_heads(heads: Dict[str, object], path: Path | str) -> Path:
    payload: Dict[str, object] = {}
    for name in ("appraisal", "vad", "policy", "general_policy", "world", "timing", "timing_gate"):
        model = heads.get(name)
        if model is None:
            continue
        if isinstance(model, TimingModel):
            payload[name] = {"kind": "timing", "state": model.to_dict()}
            continue
        if isinstance(model, CompositeAppraisalModel):
            payload[name] = {"kind": "composite_appraisal", "state": model.to_dict()}
            continue
        state = getattr(model, "reg", None) or getattr(model, "clf", None)
        if isinstance(getattr(model, "reg", None), RidgeRegressor):
            payload[name] = {"kind": "ridge", "state": state.to_dict(), "targets": list(getattr(model, "targets", []))}
        else:
            payload[name] = {"kind": "linear" if hasattr(model, "reg") else "softmax", "state": state.to_dict()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_heads(path: Path | str) -> Dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    out: Dict[str, object] = {}
    for name, item in payload.items():
        state = item["state"]
        if item["kind"] == "timing":
            out[name] = TimingModel.from_dict(state)
            continue
        if item["kind"] == "composite_appraisal":
            out[name] = CompositeAppraisalModel.from_dict(state)
            continue
        if item["kind"] == "ridge":
            reg = RidgeRegressor.from_dict(state)
            targets = tuple(item.get("targets") or ())
            model = AppraisalModel(reg.n_features, targets=targets) if targets else AppraisalModel(reg.n_features)
            model.reg = reg
            out[name] = model
            continue
        if item["kind"] == "linear":
            reg = LinearRegressor.from_dict(state)
            if name == "appraisal":
                model = AppraisalModel(reg.n_features)
            elif name == "vad":
                model = AppraisalModel(reg.n_features, targets=("valence", "arousal", "dominance"))
            elif name == "world":
                model = WorldModel(reg.n_features)
            else:
                continue
            model.reg = reg
        else:
            clf = SoftmaxRegression.from_dict(state)
            if name == "policy":
                model = PolicyModel(clf.n_features)
            elif name == "general_policy":
                model = GeneralPolicyModel(clf.n_features)
            else:
                continue
            model.clf = clf
        out[name] = model
    return out
