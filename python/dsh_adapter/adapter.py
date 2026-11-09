# -*- coding: utf-8 -*-
"""DSH adapter for BioCore: state + interpretation layer, no AstrBot.

This layer runs one turn of BioCore outside AstrBot:

    state JSON --load_state--> dict --one_turn(text)--> {reply_hint, state_after}
                                --save_state--> state JSON

It only reuses pure modules from biocore_model (the main plugin repo is
read-only here, imported through a path bootstrap):

* feedback_user -- bounded user-behaviour deltas;
* bio_tick      -- one life-maintenance tick (decay / interoception / needs);
* expr_state    -- deterministic state block text (interpretation layer);
* preset        -- structured prompt blocks (interpreter preset);
* x_directives  -- canonical expression constraints, merged conservatively.

Hard boundaries of this adapter:
- never imports astrbot or main;
- one_turn is pure: no file IO, no network, no clock, no model call
  (time is injected as a number; file IO lives only in load_state/save_state);
- it never generates reply text; reply_hint is an expression constraint
  (length / tone / initiative / allow_correction), nothing more.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# --- import bootstrap: biocore_model is imported read-only from the plugin ---
_DEFAULT_REPO = (Path(__file__).resolve().parents[1]
                 / "astrbot_plugin_biocore_standalone")
_REPO_ROOT = Path(os.environ.get("BIOCORE_MODEL_PATH") or _DEFAULT_REPO)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from biocore_model import bio_tick, expr_state, feedback_user  # noqa: E402
from biocore_model import (exit_or_silence, intero_style, metacog, preset,  # noqa: E402
                           relation_stage, x_directives)

STATE_VERSION = 1
ADAPTER_VERSION = "1.0"
UI_LEVEL = "L2"
TURN_SECONDS = 300.0
SEEN_LIMIT = 200
HISTORY_LIMIT = 240
SERIES_LIMIT = 120
MEMORY_LIMIT = 20
MEMORY_PREVIEW_LIMIT = 80
STATE_PAYLOAD_LIMIT = 16 * 1024
SERIES_WINDOWS = {"1h": 3600.0, "24h": 86400.0, "7d": 604800.0}
SERIES_FIELDS = ("v", "tension", "warmth", "heaviness", "clarity")

# UI switch key -> host config key (DSH-UI-DESIGN 7.3.7). Only these are writable.
SWITCH_MAP = {
    "safety.master": "safety_enabled",
    "safety.boundary": "boundary_enabled",
    "safety.crisis": "crisis_safety_enabled",
    "autonomy.proactive_send": "proactive_send",
}
DEFAULT_SETTINGS = {
    "safety_enabled": False,
    "boundary_enabled": True,
    "crisis_safety_enabled": True,
    "proactive_send": False,
}

# Every documented row of the /state payload (DSH-UI-DESIGN 7.2, 57 rows).
FIELD_PATHS = (
    # A. meta (7)
    "ok", "available", "adapter.version", "adapter.level", "session_id",
    "generated_at", "degraded",
    # B. first screen numbers (5)
    "v", "intero.tension", "intero.warmth", "intero.heaviness", "intero.clarity",
    # C. first screen words (3)
    "tone.label", "relation.stage", "autonomy.state",
    # D. body secondary (10)
    "arousal", "dominance", "social", "energy", "trust", "self_esteem",
    "social_pain", "circadian_alertness", "allostatic_load", "boundary_pressure",
    # E. needs / mood (6)
    "needs.belonging", "needs.esteem", "needs.control", "needs.meaning",
    "mood.valence", "mood.arousal",
    # F. relationship (4)
    "relationship.closeness", "relationship.conflict_ema",
    "relationship.bids", "attachment.style",
    # G. this turn expression (6)
    "turn.length_factor", "turn.target", "turn.cap", "turn.initiative",
    "turn.allow_correction", "turn.directive_sources",
    # H. memory (5)
    "memory.count", "memory.pinned", "memory.tombstones",
    "memory.last_recall", "memory.recent",
    # I. autonomy (4)
    "autonomy.enabled", "autonomy.ready", "autonomy.next_window_s",
    "autonomy.sent_today",
    # J. safety (4)
    "safety.master", "safety.boundary", "safety.crisis", "safety.last_block",
    # K. health / diagnostics (3)
    "health", "trace", "errors",
)

CORE_RANGES = {
    "v": (-1.0, 1.0),
    "a": (0.0, 1.0),
    "d": (-1.0, 1.0),
    "social": (0.0, 1.0),
    "stress": (0.0, 1.0),
    "energy": (0.0, 1.0),
    "sleep_debt": (0.0, 24.0),
    "allostatic_load": (0.0, 1.0),
    "conflict_ema": (0.0, 1.0),
    "social_pain": (0.0, 1.0),
    "self_esteem": (0.0, 1.0),
    "mood_valence": (-1.0, 1.0),
    "mood_arousal": (0.0, 1.0),
    "boundary_pressure": (0.0, 1.0),
    "circadian_alertness": (0.0, 1.0),
}

DEFAULT_CORE = {
    "v": 0.0,
    "a": 0.40,
    "d": 0.55,
    "social": 0.55,
    "stress": 0.20,
    "energy": 0.60,
    "sleep_debt": 0.0,
    "allostatic_load": 0.0,
    "conflict_ema": 0.0,
    "social_pain": 0.0,
    "self_esteem": 0.60,
    "mood_valence": 0.0,
    "mood_arousal": 0.40,
    "boundary_pressure": 0.0,
    "circadian_alertness": 0.50,
    "labels": {"emotion": "neutral"},
    "needs": None,
    "intero": None,
    "pending_intent": None,
    "emotion_decay_factors": {},
    "valence_decay_minutes": 120.0,
    "arousal_decay_minutes": 30.0,
    "social_decay_minutes": 180.0,
    "last_bio_tick_ts": 0.0,
}

DEFAULT_MEMORY = {
    "count": 0,
    "pinned": 0,
    "tombstones": 0,
    "last_recall": None,
    "recent": [],
}
DEFAULT_AUTONOMY = {
    "enabled": False,
    "ready": None,
    "next_window_s": None,
    "sent_today": 0,
    "hourly_cap": 3,
    "cooldown_until": 0.0,
}
DEFAULT_STATE = {
    "version": STATE_VERSION,
    "turn": 0,
    "now": 0.0,
    "session_id": "",
    "days_known": 30.0,
    "core": DEFAULT_CORE,
    "relationship": {"closeness": 0.5, "trust": 0.5, "conflict": 0.0,
                     "bids": 0.0, "attachment_style": "secure"},
    "memory": DEFAULT_MEMORY,
    "settings": DEFAULT_SETTINGS,
    "autonomy": DEFAULT_AUTONOMY,
    "history": [],
    "last_turn": {},
    "seen": {},
}

# Deterministic, adapter-local text classifier. It is a keyword table, not a
# model: the point is to exercise the feedback channel without any LLM call.
_KIND_WORDS = (
    ("insult", ("滚", "烦死", "闭嘴", "讨厌你", "废物", "蠢")),
    ("reject", ("不想聊", "别说了", "不说了", "算了", "走开")),
    ("affection", ("抱抱", "想你", "喜欢你", "爱你", "贴贴")),
    ("praise", ("谢谢", "厉害", "好棒", "有用", "喜欢", "开心")),
    ("sad", ("难过", "累", "难受", "撑不住", "想哭", "焦虑", "压力大")),
)


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _clip(value, low=0.0, high=1.0):
    try:
        return max(low, min(high, float(value)))
    except Exception:
        return low


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_jsonable(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _jsonable(item)
                   for key, item in value.items())
    return False


def default_state(now=0.0):
    """A fresh, self-contained state dict (caller owns persistence)."""
    state = copy.deepcopy(DEFAULT_STATE)
    state["now"] = float(now or 0.0)
    return state


def _normalize_core(raw):
    core = copy.deepcopy(DEFAULT_CORE)
    data = raw if isinstance(raw, dict) else {}
    for key, (low, high) in CORE_RANGES.items():
        core[key] = round(_clip(_num(data.get(key, core[key])), low, high), 4)
    labels = data.get("labels")
    core["labels"] = dict(labels) if isinstance(labels, dict) else {"emotion": "neutral"}
    for key in ("needs", "intero", "pending_intent"):
        value = data.get(key)
        core[key] = copy.deepcopy(value) if isinstance(value, (dict, list)) else None
    emotion_factors = data.get("emotion_decay_factors")
    core["emotion_decay_factors"] = (dict(emotion_factors)
                                     if isinstance(emotion_factors, dict) else {})
    for key, fallback in (("valence_decay_minutes", 120.0),
                          ("arousal_decay_minutes", 30.0),
                          ("social_decay_minutes", 180.0),
                          ("last_bio_tick_ts", 0.0)):
        core[key] = _num(data.get(key, fallback), fallback)
    return core


def _merged_settings(raw):
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        for key in settings:
            if key in raw:
                settings[key] = bool(raw.get(key))
    return settings


def normalize_state(raw):
    """Coerce a loose dict into the adapter state contract (no IO)."""
    data = raw if isinstance(raw, dict) else {}
    state = default_state(_num(data.get("now"), 0.0))
    state["version"] = STATE_VERSION
    state["turn"] = int(max(0, _num(data.get("turn"), 0.0)))
    state["session_id"] = str(data.get("session_id") or "")[:64]
    state["days_known"] = round(max(0.0, _num(data.get("days_known"), 30.0)), 3)
    state["core"] = _normalize_core(data.get("core"))
    relation = data.get("relationship")
    rel = relation if isinstance(relation, dict) else {}
    state["relationship"] = {
        "closeness": round(_clip(_num(rel.get("closeness"), 0.5)), 4),
        "trust": round(_clip(_num(rel.get("trust"), 0.5)), 4),
        "conflict": round(_clip(_num(rel.get("conflict"), 0.0)), 4),
        "bids": round(max(0.0, _num(rel.get("bids"), 0.0)), 3),
        "attachment_style": str(rel.get("attachment_style") or "secure")[:16],
    }
    memory = data.get("memory")
    mem = memory if isinstance(memory, dict) else {}
    recent = []
    for item in (mem.get("recent") or [])[:MEMORY_LIMIT]:
        if isinstance(item, dict):
            recent.append(copy.deepcopy(item))
    state["memory"] = {
        "count": int(max(0.0, _num(mem.get("count"), 0.0))),
        "pinned": int(max(0.0, _num(mem.get("pinned"), 0.0))),
        "tombstones": int(max(0.0, _num(mem.get("tombstones"), 0.0))),
        "last_recall": (copy.deepcopy(mem.get("last_recall"))
                        if isinstance(mem.get("last_recall"), dict) else None),
        "recent": recent,
    }
    state["settings"] = _merged_settings(data.get("settings"))
    autonomy = data.get("autonomy")
    auto = autonomy if isinstance(autonomy, dict) else {}
    ready = auto.get("ready")
    ready = (_clip(_num(ready), 0.0, 1.0)
             if isinstance(ready, (int, float)) else None)
    state["autonomy"] = {
        "enabled": bool(auto.get("enabled", DEFAULT_AUTONOMY["enabled"])),
        "ready": None if ready is None else round(ready, 3),
        "next_window_s": (int(max(0.0, _num(auto.get("next_window_s"), 0.0)))
                          if auto.get("next_window_s") is not None else None),
        "sent_today": int(max(0.0, _num(auto.get("sent_today"), 0.0))),
        "hourly_cap": int(max(0.0, _num(auto.get("hourly_cap"), 3.0))),
        "cooldown_until": round(max(0.0, _num(auto.get("cooldown_until"), 0.0)), 3),
    }
    history = []
    for sample in (data.get("history") or [])[-HISTORY_LIMIT:]:
        if isinstance(sample, dict):
            history.append(copy.deepcopy(sample))
    state["history"] = history
    trace = data.get("last_turn")
    state["last_turn"] = copy.deepcopy(trace) if isinstance(trace, dict) else {}
    errors = []
    for item in (data.get("errors") or [])[:3]:
        if isinstance(item, dict):
            errors.append(copy.deepcopy(item))
    state["errors"] = errors
    seen = data.get("seen")
    state["seen"] = ({str(key): _num(value, 0.0)
                      for key, value in seen.items()}
                     if isinstance(seen, dict) else {})
    return state


def classify_user_text(text):
    """Map user text to a feedback kind with a fixed keyword table."""
    body = str(text or "")
    lowered = body.lower()
    for kind, words in _KIND_WORDS:
        for word in words:
            if word in body or word in lowered:
                return kind
    return "reply"


def _message_id(text, turn):
    digest = hashlib.sha256(("%s:%s" % (turn, text)).encode("utf-8")).hexdigest()
    return digest[:16]


FEEDBACK_KIND_MAP = {"sad": "sad_empathy"}


def _apply_feedback(core, text, now, seen, turn):
    """Apply FB-2 user-behaviour deltas; returns the applied audit rows."""
    kind = classify_user_text(text)
    feedback_kind = FEEDBACK_KIND_MAP.get(kind, kind)
    result = feedback_user.on_message(
        core,
        {"kind": feedback_kind, "id": _message_id(text, turn), "ts": now},
        now,
        seen,
    )
    applied = {}
    for field, delta in (result.get("deltas") or {}).items():
        low, high = feedback_user.RANGES.get(field, (0.0, 1.0))
        old = _num(core.get(field), 0.0)
        new = round(_clip(old + _num(delta), low, high), 4)
        core[field] = new
        applied[field] = new
    if len(seen) > SEEN_LIMIT:
        keep = sorted(seen.items(), key=lambda item: _num(item[1]))[-SEEN_LIMIT:]
        seen.clear()
        seen.update(keep)
    return {"kind": kind, "deltas": result.get("deltas") or {},
            "idempotent": bool(result.get("idempotent")), "applied": applied}


def _run_tick(core, now):
    """Run one bio_tick on a namespace and copy the results back."""
    ns = SimpleNamespace(**copy.deepcopy(core))
    report = bio_tick.bio_tick(ns, now=now)
    updated = {key: value for key, value in vars(ns).items() if _jsonable(value)}
    core.update(updated)
    action = report.get("action")
    return {
        "changed": bool(report.get("changed")),
        "idempotent": bool(report.get("idempotent")),
        "discomfort": round(_num(report.get("discomfort"), 0.0), 4),
        "action": (action or {}).get("action") if isinstance(action, dict) else action,
    }


def _relationship_text(relationship):
    rel = relationship if isinstance(relationship, dict) else {}
    closeness = _num(rel.get("closeness"), 0.5)
    conflict = _num(rel.get("conflict"), 0.0)
    if conflict >= 0.5:
        return "关系：还没和好，说话要小心"
    if closeness >= 0.7:
        return "关系：很近，可以软一点"
    if closeness <= 0.35:
        return "关系：还生疏，别太热"
    return "关系：普通，保持分寸"


def _build_constraints(core, relationship, confidence):
    """Merge three directive producers into one canonical hint."""
    exit_hint = exit_or_silence.directives(exit_or_silence.exit_or_silence(
        {"energy": _num(core.get("energy"), 0.6),
         "stress": _num(core.get("stress"), 0.2),
         "v": _num(core.get("v"), 0.0)},
        relationship,
    ))
    metacog_hint = metacog.directives(metacog.evaluate(
        confidence=confidence, guard_hits=0,
    ))
    stress = _num(core.get("stress"), 0.2)
    valence = _num(core.get("v"), 0.0)
    closeness = _num((relationship or {}).get("closeness"), 0.5)
    state_hint = x_directives.normalize({
        "length_scale": round(_clip(1.15 - 0.60 * stress, 0.50, 1.50), 3),
        "initiative": round(_clip(0.50 + 0.35 * valence
                                  - 0.25 * stress
                                  + 0.10 * (closeness - 0.5), 0.0, 1.0), 3),
        "tone": ("短一点，别铺开" if stress >= 0.65
                 else "稳一点" if valence <= -0.35 else ""),
        "allow_clarify": bool(confidence < 0.40),
        "note": "状态派生",
    }, "state")
    merged = x_directives.merge_named([
        ("exit_or_silence", exit_hint),
        ("metacog", metacog_hint),
        ("state", state_hint),
    ])
    merged["confidence"] = round(confidence, 3)
    return merged


def _prompt_blocks(state_block_text, relationship):
    variables = {
        "state_block": state_block_text,
        "relationship": _relationship_text(relationship),
    }
    assembled = preset.assemble(variables, history_len=0)
    blocks = []
    for position, depth, role, content in preset.render_texts(assembled):
        blocks.append({"position": position, "depth": int(depth),
                       "role": role, "content": content})
    return blocks


def _append_history(history, core, now):
    samples = [dict(item) for item in (history or []) if isinstance(item, dict)]
    intero = core.get("intero") if isinstance(core.get("intero"), dict) else {}
    samples.append({
        "ts": round(float(now), 3),
        "v": round(_num(core.get("v"), 0.0), 4),
        "tension": round(_num(intero.get("tension"), 0.0), 4),
        "warmth": round(_num(intero.get("warmth"), 0.0), 4),
        "heaviness": round(_num(intero.get("heaviness"), 0.0), 4),
        "clarity": round(_num(intero.get("clarity"), 0.0), 4),
    })
    return samples[-HISTORY_LIMIT:]


def _metacog_band(confidence):
    if confidence >= 0.65:
        return "高"
    if confidence >= 0.40:
        return "中"
    return "低"


def _build_trace(core_before, core_after, reply_hint, blocks, feedback, confidence):
    deltas = []
    feedback_fields = set((feedback.get("deltas") or {}).keys())
    for field in CORE_RANGES:
        before = round(_num(core_before.get(field), 0.0), 4)
        after = round(_num(core_after.get(field), before), 4)
        if abs(after - before) < 0.0005:
            continue
        deltas.append({"field": field, "before": before, "after": after,
                       "source": "user" if field in feedback_fields else "bio_tick"})
        if len(deltas) >= 12:
            break
    labels = core_after.get("labels") if isinstance(core_after.get("labels"), dict) else {}
    return {
        "state_delta": deltas,
        "intent": str(labels.get("intent") or "hold"),
        "metacog_band": _metacog_band(confidence),
        "length_source": list(reply_hint.get("sources") or []),
        "prompt_diff": [{"position": block.get("position"),
                         "chars": len(str(block.get("content") or ""))}
                        for block in blocks],
    }


def one_turn(text, state, now=None):
    """Run one BioCore turn: user text -> constraints + state_after.

    Pure: time is injected via now (or advanced by TURN_SECONDS from the
    state), and no file/network/clock/model access happens here.
    """
    state_in = normalize_state(state)
    base_now = _num(state_in.get("now"), 0.0)
    now = float(base_now + TURN_SECONDS) if now is None else float(now)
    turn = int(state_in.get("turn", 0))
    core = state_in["core"]
    core_before = copy.deepcopy(core)
    relationship = dict(state_in.get("relationship") or {})
    seen = dict(state_in.get("seen") or {})

    feedback = _apply_feedback(core, text, now, seen, turn)
    tick = _run_tick(core, now)

    confidence = round(_clip(
        0.72 - 0.45 * _num(core.get("stress"), 0.2)
        + 0.20 * (_num(core.get("social"), 0.55) - 0.5)
        - 0.10 * abs(_num(core.get("v"), 0.0)), 0.05, 0.95), 3)

    snapshot = {
        "v": core.get("v"), "a": core.get("a"), "d": core.get("d"),
        "social": core.get("social"), "stress": core.get("stress"),
        "energy": core.get("energy"), "sleep_debt": core.get("sleep_debt"),
        "allostatic_load": core.get("allostatic_load"),
        "needs": core.get("needs"), "intero": core.get("intero"),
        "labels": core.get("labels"),
    }
    state_block = expr_state.render_state_block(
        snapshot, relationship, now=now)
    state_block_text = str(state_block.get("text") or "")

    reply_hint = _build_constraints(core, relationship, confidence)
    blocks = _prompt_blocks(state_block_text, relationship)

    state_after = copy.deepcopy(state_in)
    state_after["turn"] = turn + 1
    state_after["now"] = now
    state_after["core"] = core
    state_after["relationship"] = relationship
    state_after["seen"] = seen
    state_after["history"] = _append_history(
        state_in.get("history"), core, now)
    trace = _build_trace(core_before, core, reply_hint, blocks, feedback, confidence)
    trace["turn"] = _turn_view(core, reply_hint)
    state_after["last_turn"] = trace

    return {
        "reply_hint": reply_hint,
        "state_after": state_after,
        "prompt_blocks": blocks,
        "debug": {
            "kind": feedback["kind"],
            "deltas": feedback["deltas"],
            "feedback_idempotent": feedback["idempotent"],
            "tick": tick,
            "confidence": confidence,
            "state_block": state_block_text,
            "state_block_parts": state_block.get("parts_used") or [],
        },
    }


# ---------------------------------------------------------------------------
# DSH UI projection (DSH-UI-DESIGN section 7): data shapes only, no HTTP.
# ---------------------------------------------------------------------------
def _dig(obj, path, default=None):
    current = obj
    for part in str(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _tone_label(intero):
    directive = intero_style.tone(intero if isinstance(intero, dict) else {})
    if not directive:
        return ""
    labels = {
        intero_style.TONE_LINES["tension"]: "先接住情绪",
        intero_style.TONE_LINES["heaviness"]: "慢一点说",
        intero_style.TONE_LINES["warmth"]: "亲近一点",
        intero_style.TONE_LINES["clarity"]: "没听清先确认",
    }
    return labels.get(str(directive), "")


def _relation_stage(state, relationship):
    rel = relationship if isinstance(relationship, dict) else {}
    closeness = _num(rel.get("closeness"), 0.5)
    trust = _num(rel.get("trust"), 0.5)
    conflict = _num(rel.get("conflict"), 0.0)
    stage_state = {
        "interactions": closeness * 8.0,
        "disclosures": trust * 5.0,
        "repairs": max(0.0, 1.0 - conflict) * 1.2,
        "days_known": _num(state.get("days_known"), 30.0),
        "conflict": conflict,
    }
    try:
        return str(relation_stage.evaluate(stage_state).get("stage") or "陌生")
    except Exception:
        return "陌生"


def _autonomy_view(state, now):
    core = state.get("core") if isinstance(state.get("core"), dict) else {}
    auto = state.get("autonomy") if isinstance(state.get("autonomy"), dict) else {}
    rel = state.get("relationship") if isinstance(state.get("relationship"), dict) else {}
    settings = _merged_settings(state.get("settings"))
    ready = auto.get("ready")
    if not isinstance(ready, (int, float)):
        ready = _clip(0.30
                      + 0.45 * (1.0 - _num(core.get("social"), 0.55))
                      + 0.25 * _num(core.get("stress"), 0.2)
                      - 0.20 * _num(rel.get("closeness"), 0.5)
                      + 0.10 * max(0.0, -_num(core.get("v"), 0.0)), 0.0, 1.0)
    ready = round(_clip(ready), 3)
    cooldown = _num(auto.get("cooldown_until"), 0.0)
    next_window = auto.get("next_window_s")
    if cooldown > now:
        label = "冷却"
        next_window = int(round(cooldown - now))
    elif ready < 0.40:
        label = "静"
    elif ready < 0.60:
        label = "等"
    else:
        label = "想联系"
    return {
        "state": label,
        "enabled": bool(auto.get("enabled", settings.get("proactive_send", False))),
        "ready": ready,
        "next_window_s": (int(max(0.0, _num(next_window, 0.0)))
                          if next_window is not None else None),
        "sent_today": int(max(0.0, _num(auto.get("sent_today"), 0.0))),
        "hourly_cap": int(max(0.0, _num(auto.get("hourly_cap"), 3.0))),
    }


def _turn_view(core, reply_hint):
    intero = core.get("intero") if isinstance(core.get("intero"), dict) else {}
    style = intero_style.style(intero if isinstance(intero, dict) else {},
                               target=10, cap=30)
    return {
        "length_factor": round(_num(style.get("factor"), 1.0), 3),
        "target": int(_num(style.get("target"), 10)),
        "cap": int(_num(style.get("cap"), 30)),
        "initiative": round(_num(reply_hint.get("initiative"), 0.5), 3),
        "allow_correction": bool(reply_hint.get("allow_correction", False)),
        "directive_sources": [str(name) for name in (reply_hint.get("sources") or [])],
    }


def _memory_view(state):
    memory = state.get("memory") if isinstance(state.get("memory"), dict) else {}
    recent = []
    for item in (memory.get("recent") or [])[:MEMORY_LIMIT]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("text_preview") or "")
        recent.append({
            "id": str(item.get("id") or "")[:64],
            "ts": round(_num(item.get("ts"), 0.0), 3),
            "score": round(_num(item.get("score"), 0.0), 3),
            "source": str(item.get("source") or "")[:24],
            "kind": str(item.get("kind") or "")[:24],
            "text_preview": text[:MEMORY_PREVIEW_LIMIT],
        })
    last_recall = memory.get("last_recall")
    if isinstance(last_recall, dict):
        last_recall = {
            "query": str(last_recall.get("query") or "")[:MEMORY_PREVIEW_LIMIT],
            "hits": int(max(0.0, _num(last_recall.get("hits"), 0.0))),
            "latency_ms": round(max(0.0, _num(last_recall.get("latency_ms"), 0.0)), 1),
        }
    else:
        last_recall = None
    return {
        "count": int(max(0.0, _num(memory.get("count"), 0.0))),
        "pinned": int(max(0.0, _num(memory.get("pinned"), 0.0))),
        "tombstones": int(max(0.0, _num(memory.get("tombstones"), 0.0))),
        "last_recall": last_recall,
        "recent": recent,
    }


def _safety_view(state):
    settings = _merged_settings(state.get("settings"))
    safety = state.get("safety") if isinstance(state.get("safety"), dict) else {}
    last_block = safety.get("last_block")
    if not isinstance(last_block, dict):
        last_block = None
    return {
        "master": bool(settings.get("safety_enabled")),
        "boundary": bool(settings.get("boundary_enabled")),
        "crisis": bool(settings.get("crisis_safety_enabled")),
        "last_block": last_block,
    }


def _health_view(state, now):
    core = state.get("core") if isinstance(state.get("core"), dict) else {}
    last_tick = _num(core.get("last_bio_tick_ts"), 0.0)
    if last_tick <= 0:
        last_tick = None
    return {
        "service": "ok",
        "store": "ok",
        "last_tick_ts": last_tick,
        "tick_interval_s": 60,
    }


def _trace_view(state):
    saved = state.get("last_turn") if isinstance(state.get("last_turn"), dict) else {}
    if not saved:
        return None
    return {
        "state_delta": list(saved.get("state_delta") or []),
        "intent": str(saved.get("intent") or "hold"),
        "metacog_band": str(saved.get("metacog_band") or ""),
        "length_source": list(saved.get("length_source") or []),
        "prompt_diff": list(saved.get("prompt_diff") or []),
    }


def _errors_view(state):
    errors = []
    for item in (state.get("errors") or [])[:3]:
        if not isinstance(item, dict):
            continue
        errors.append({
            "code": str(item.get("code") or "")[:64],
            "message": str(item.get("message") or "")[:120],
            "ts": int(_num(item.get("ts"), 0.0)),
        })
    return errors


def _unavailable_payload(session_id, reason):
    return {
        "ok": True,
        "available": False,
        "adapter": {"version": ADAPTER_VERSION, "level": UI_LEVEL},
        "session_id": str(session_id or ""),
        "generated_at": 0,
        "degraded": ["state:" + str(reason)],
        "reason": str(reason),
        "v": None,
        "intero": {"tension": None, "warmth": None,
                   "heaviness": None, "clarity": None},
        "tone": {"label": ""},
        "relation": {"stage": ""},
        "autonomy": {"state": "", "enabled": False, "ready": None,
                     "next_window_s": None, "sent_today": 0, "hourly_cap": 0},
        "arousal": None,
        "dominance": None,
        "social": None,
        "energy": None,
        "trust": None,
        "self_esteem": None,
        "social_pain": None,
        "circadian_alertness": None,
        "allostatic_load": None,
        "boundary_pressure": None,
        "needs": {"belonging": None, "esteem": None,
                  "control": None, "meaning": None},
        "mood": {"valence": None, "arousal": None},
        "relationship": {"closeness": None, "conflict_ema": None, "bids": None},
        "attachment": {"style": ""},
        "turn": {"length_factor": None, "target": None, "cap": None,
                 "initiative": None, "allow_correction": False,
                 "directive_sources": []},
        "memory": {"count": 0, "pinned": 0, "tombstones": 0,
                   "last_recall": None, "recent": []},
        "safety": {"master": False, "boundary": False,
                   "crisis": False, "last_block": None},
        "health": {"service": "unknown", "store": "unknown",
                   "last_tick_ts": None, "tick_interval_s": None},
        "trace": None,
        "errors": [],
    }


def state_payload(state, session_id="", settings=None,
                  include_trace=False, generated_at=None):
    """Project a state dict onto the DSH /state contract (57 rows).

    Pure and read-only: no IO, no clock, no model call.
    """
    if not isinstance(state, dict):
        return _unavailable_payload(session_id, "no_state_yet")
    data = normalize_state(state)
    own_session = str(data.get("session_id") or "")
    wanted = str(session_id or "")
    if wanted and own_session and wanted != own_session:
        return _unavailable_payload(wanted, "no_state_yet")

    core = data["core"]
    relationship = data["relationship"]
    intero = core.get("intero") if isinstance(core.get("intero"), dict) else {}
    needs = core.get("needs") if isinstance(core.get("needs"), dict) else {}
    saved_turn = data.get("last_turn") if isinstance(data.get("last_turn"), dict) else {}
    saved_turn_view = (saved_turn.get("turn")
                       if isinstance(saved_turn.get("turn"), dict) else {})
    effective_settings = _merged_settings(
        settings if isinstance(settings, dict) else data.get("settings"))
    now = _num(data.get("now"), 0.0)
    if generated_at is None:
        generated_at = int(round(now * 1000.0))
    autonomy_view = _autonomy_view(data, now)
    safety_view = _safety_view({"settings": effective_settings,
                                "safety": {"last_block": state.get("safety", {}).get("last_block")
                                           if isinstance(state.get("safety"), dict) else None}})
    turn_view = saved_turn_view if saved_turn_view else _turn_view(core, {})
    mood_valence = _num(core.get("mood_valence"), 0.0)
    if abs(mood_valence) < 1e-9:
        mood_valence = _num(core.get("v"), 0.0)
    payload = {
        "ok": True,
        "available": True,
        "adapter": {"version": ADAPTER_VERSION, "level": UI_LEVEL},
        "session_id": wanted or own_session,
        "generated_at": int(generated_at),
        "degraded": [],
        "v": round(_num(core.get("v"), 0.0), 2),
        "intero": {
            "tension": round(_num(intero.get("tension"), 0.0), 2),
            "warmth": round(_num(intero.get("warmth"), 0.0), 2),
            "heaviness": round(_num(intero.get("heaviness"), 0.0), 2),
            "clarity": round(_num(intero.get("clarity"), 0.0), 2),
        },
        "tone": {"label": _tone_label(intero)},
        "relation": {"stage": _relation_stage(data, relationship)},
        "autonomy": autonomy_view,
        "arousal": round(_num(core.get("a"), 0.0), 2),
        "dominance": round(_num(core.get("d"), 0.0), 2),
        "social": round(_num(core.get("social"), 0.0), 2),
        "energy": round(_num(core.get("energy"), 0.0), 2),
        "trust": round(_num(relationship.get("trust"), 0.5), 2),
        "self_esteem": round(_num(core.get("self_esteem"), 0.0), 2),
        "social_pain": round(_num(core.get("social_pain"), 0.0), 2),
        "circadian_alertness": round(_num(core.get("circadian_alertness"), 0.0), 2),
        "allostatic_load": round(_num(core.get("allostatic_load"), 0.0), 2),
        "boundary_pressure": round(_num(core.get("boundary_pressure"), 0.0), 2),
        "needs": {
            "belonging": round(_num(needs.get("belonging"), 0.0), 2),
            "esteem": round(_num(needs.get("self_esteem"), 0.0), 2),
            "control": round(_num(needs.get("control"), 0.0), 2),
            "meaning": round(_num(needs.get("meaningful_existence"), 0.0), 2),
        },
        "mood": {"valence": round(mood_valence, 2),
                 "arousal": round(_num(core.get("mood_arousal"),
                                       core.get("a")), 2)},
        "relationship": {
            "closeness": round(_num(relationship.get("closeness"), 0.5), 2),
            "conflict_ema": round(_num(core.get("conflict_ema"), 0.0), 2),
            "bids": round(_num(relationship.get("bids"), 0.0), 2),
        },
        "attachment": {"style": str(relationship.get("attachment_style") or "secure")[:16]},
        "turn": turn_view,
        "memory": _memory_view(data),
        "safety": safety_view,
        "health": _health_view(data, now),
        "trace": None,
        "errors": _errors_view(data),
    }
    if include_trace:
        payload["trace"] = _trace_view(data)
    return payload


def _session_mismatch(data, session_id):
    own = str((data or {}).get("session_id") or "")
    wanted = str(session_id or "")
    return bool(wanted and own and wanted != own)


def _generated_ms(state, generated_at):
    if generated_at is not None:
        return int(generated_at)
    return int(round(_num((state or {}).get("now"), 0.0) * 1000.0))


def series_payload(state, session_id="", window="1h", generated_at=None):
    """5 first-screen curves, capped at SERIES_LIMIT points per field."""
    if not isinstance(state, dict):
        return {"ok": True, "available": False, "session_id": str(session_id or ""),
                "window": str(window), "generated_at": 0, "series": {},
                "reason": "no_state_yet", "degraded": ["state:no_state_yet"]}
    data = normalize_state(state)
    if _session_mismatch(data, session_id):
        return {"ok": True, "available": False, "session_id": str(session_id),
                "window": str(window), "generated_at": 0, "series": {},
                "reason": "no_state_yet", "degraded": ["state:no_state_yet"]}
    window = str(window) if str(window) in SERIES_WINDOWS else "1h"
    span = SERIES_WINDOWS[window]
    now = _num(data.get("now"), 0.0)
    samples = [item for item in (data.get("history") or [])
               if isinstance(item, dict) and _num(item.get("ts"), 0.0) >= now - span]
    series = {}
    if samples:
        stride = max(1, int(math.ceil(len(samples) / float(SERIES_LIMIT))))
        kept = samples[::stride][-SERIES_LIMIT:]
        for field in SERIES_FIELDS:
            series[field] = [
                {"ts": int(round(_num(item.get("ts"), 0.0) * 1000.0)),
                 "value": round(_num(item.get(field), 0.0), 3)}
                for item in kept
            ]
    return {"ok": True, "available": bool(samples),
            "session_id": str(session_id or data.get("session_id") or ""),
            "window": window, "generated_at": _generated_ms(data, generated_at),
            "series": series, "degraded": []}


def memory_payload(state, session_id="", limit=20):
    """Recent memory previews (each text <= 80 chars, at most 20 items)."""
    if not isinstance(state, dict):
        return {"ok": True, "available": False, "session_id": str(session_id or ""),
                "generated_at": 0, "count": 0, "pinned": 0, "tombstones": 0,
                "last_recall": None, "recent": [],
                "reason": "no_state_yet", "degraded": ["state:no_state_yet"]}
    data = normalize_state(state)
    if _session_mismatch(data, session_id):
        return {"ok": True, "available": False, "session_id": str(session_id),
                "generated_at": 0, "count": 0, "pinned": 0, "tombstones": 0,
                "last_recall": None, "recent": [],
                "reason": "no_state_yet", "degraded": ["state:no_state_yet"]}
    view = _memory_view(data)
    cap = max(0, min(int(_num(limit, MEMORY_LIMIT)), MEMORY_LIMIT))
    view["recent"] = view["recent"][:cap]
    payload = {"ok": True, "available": True,
               "session_id": str(session_id or data.get("session_id") or ""),
               "generated_at": _generated_ms(data, None)}
    payload.update(view)
    payload["degraded"] = []
    return payload


def trace_payload(state, session_id=""):
    """This-turn diagnostics (diag_trace shape); read-only projection."""
    if not isinstance(state, dict):
        return {"ok": True, "available": False, "session_id": str(session_id or ""),
                "generated_at": 0, "trace": None,
                "reason": "no_state_yet", "degraded": ["state:no_state_yet"]}
    data = normalize_state(state)
    if _session_mismatch(data, session_id):
        return {"ok": True, "available": False, "session_id": str(session_id),
                "generated_at": 0, "trace": None,
                "reason": "no_state_yet", "degraded": ["state:no_state_yet"]}
    trace = _trace_view(data)
    return {"ok": True, "available": bool(trace),
            "session_id": str(session_id or data.get("session_id") or ""),
            "generated_at": _generated_ms(data, None),
            "trace": trace, "degraded": []}


def health_payload(state=None, session_id="", service="ok", store="ok", now=None):
    """Adapter/service health; readable even when no state exists."""
    if isinstance(state, dict):
        data = normalize_state(state)
        view = _health_view(data, _num(now, data.get("now")))
        available = True
        last_tick = view["last_tick_ts"]
    else:
        available = False
        last_tick = None
    payload = {
        "ok": True,
        "available": available,
        "service": str(service or "unknown"),
        "store": str(store or "unknown"),
        "last_tick_ts": last_tick,
        "tick_interval_s": 60,
        "adapter": {"version": ADAPTER_VERSION, "level": UI_LEVEL},
        "session_id": str(session_id or ""),
        "degraded": [] if available else ["state:no_state_yet"],
    }
    return payload


def switch_request(body):
    """Validate a /switch request: 4 keys only, boolean, confirm == key."""
    data = body if isinstance(body, dict) else {}
    key = str(data.get("key") or "")
    if key not in SWITCH_MAP:
        return {"ok": False, "code": "unknown_key",
                "message": "unknown switch key", "allowed": sorted(SWITCH_MAP)}
    if not isinstance(data.get("value"), bool):
        return {"ok": False, "code": "invalid_value",
                "message": "value must be boolean"}
    if str(data.get("confirm") or "") != key:
        return {"ok": False, "code": "confirm_required",
                "message": "confirm must equal key"}
    return {"ok": True, "key": key, "config_key": SWITCH_MAP[key],
            "value": data["value"],
            "settings_patch": {SWITCH_MAP[key]: data["value"]}}


def apply_switch(state, body):
    """Apply a validated switch to the settings section only (pure).

    The host settings document stays authoritative; this returns the patch
    plus a state copy for the caller to persist, and never touches other
    state sections or any file.
    """
    request = switch_request(body)
    if not request.get("ok"):
        return request
    data = normalize_state(state)
    settings = _merged_settings(data.get("settings"))
    settings[request["config_key"]] = request["value"]
    data["settings"] = settings
    result = dict(request)
    result["settings"] = settings
    result["state_after"] = data
    return result


# The six documented DSH endpoints (DSH-UI-DESIGN 7.1); no HTTP layer here.
ENDPOINTS = {
    "/api/biocore/state": state_payload,
    "/api/biocore/series": series_payload,
    "/api/biocore/memory": memory_payload,
    "/api/biocore/trace": trace_payload,
    "/api/biocore/health": health_payload,
    "/api/biocore/switch": switch_request,
}


def _as_path(store):
    if isinstance(store, Path):
        return store
    return Path(str(store))


def load_state(store):
    """Load state JSON. Missing file -> fresh default; malformed JSON -> error."""
    if isinstance(store, dict):
        return normalize_state(store)
    path = _as_path(store)
    if not path.exists():
        return default_state()
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("state file is not valid JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise ValueError("state file must contain a JSON object")
    return normalize_state(data)


def save_state(state, store):
    """Atomically write state JSON (the only persistence in this module)."""
    path = _as_path(store)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalize_state(state), ensure_ascii=False,
                         indent=2, sort_keys=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def state_summary(state):
    """Compact diff-friendly view for CLI/demo output."""
    core = (state or {}).get("core") or {}
    fields = ("v", "a", "d", "social", "stress", "energy",
              "social_pain", "self_esteem", "mood_valence")
    return {"turn": (state or {}).get("turn"),
            "now": (state or {}).get("now"),
            "core": {key: round(_num(core.get(key), 0.0), 3) for key in fields}}


__all__ = [
    "TURN_SECONDS", "ADAPTER_VERSION", "UI_LEVEL", "FIELD_PATHS",
    "SWITCH_MAP", "DEFAULT_SETTINGS", "SERIES_LIMIT", "MEMORY_LIMIT",
    "MEMORY_PREVIEW_LIMIT", "STATE_PAYLOAD_LIMIT",
    "default_state", "normalize_state", "load_state", "save_state",
    "one_turn", "classify_user_text", "state_summary",
    "state_payload", "series_payload", "memory_payload", "trace_payload",
    "health_payload", "switch_request", "apply_switch", "ENDPOINTS",
]
