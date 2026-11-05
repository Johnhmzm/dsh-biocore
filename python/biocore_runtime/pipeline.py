# -*- coding: utf-8 -*-
"""BioCore runtime pipeline: capture / inject / gate / tools policy in Python.

CONTRACT
--------
This module is the single policy owner. Node (DSH) only forwards JSON and
executes the returned action. It never imports astrbot and never renders a full
prompt by itself.

CLI (one JSON line in stdin, one JSON line out):

    python biocore_runtime/pipeline.py <action>
      inject        in {phase, session_id, user_text, state, switches}
                    out {ok, text, phase, chars, sections}
      gate          in {reply, user_text, switches}
                    out {ok, blocked, action: pass|replace, text, rule, label}
      capture       in {session_id, role, text, switches, store?}
                    out {ok, captured, reason, entry_id?}
      tool          in {name, args, session_id, switches, store?}
                    out {ok, result}   (result 不含 ok，内层 ok 拍平到外层)
      memory_inject in {session_id, store?, max_chars?, top_k?}
                    out {ok, text, chars, ids}
      trace         in {session_id, store?, limit?}
                    out {ok, rows}  (read-only; rows are trace JSONL entries)

turn_end writes one diagnostic row to <store>/traces/<session_id>.jsonl,
keeping the newest 50; diagnostics never change gate/capture behaviour.
      proactive_tick in {session_id, last_human_ts, switches, store?, now?}
                    out {ok, should_send, text, reason, gap_minutes}

Four switch keys, all default false in every environment:
  safety_enabled / boundary_enabled / crisis_safety_enabled / proactive_send
proactive_autonomy is accepted only as a read-side alias for proactive_send.

Relationship is user-level, not session-level (relationship_shared defaults true):
<store>/relationship.json holds trust/closeness/conflict/attachment_style/bids/
days_known; turn_start merges it into the session state and turn_end folds the
bounded per-turn change back (trust/closeness/conflict <= 0.02 per turn, versioned
+ lock file; relationship_shared=false keeps the old per-session behaviour).
Session state stays <store>/sessions/<session_id>/state.json for mood/stress/etc.

Storage layout: <store>/sessions/<session_id>/memory.json and
<store>/sessions/<session_id>/capture_state.json. Default store root comes from
payload.store, then env BIOCORE_MEMORY_STORE, then <plugin>/data/memory.

Python functions (import path: biocore_runtime.pipeline):
  capture_decision(session_id, role, text, state) -> {capture, reason, ...}
  capture(session_id, role, text, store, config=None) -> {ok, decision, entry}
  inject_block(session_id, store, max_chars=420, top_k=5) -> {text, chars, ids}
  gate_reply(reply_text, flags) -> {hit, blocked, action, rule, text}
  prompt_block(payload) -> {ok, phase, text, chars, sections}
  tool_call(name, args, store) -> remember / recall / forget result
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path

# Running as a script: make the plugin root importable for biocore_model.
_PLUGIN_ROOT = str(Path(__file__).resolve().parents[1])
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

MAX_CHARS = 420
TOP_K = 5
INJECT_MAX_K = 10          # inject_block may ask for more when rerank prunes
INJECT_MODES = ("query", "recent")
HOURLY_CAP = 20
MIN_CHARS = 8
MAX_TEXT_CHARS = 2000
MAX_STATE_CHARS = 200
MAX_ENTRIES = 500
SESSION_MAX_LEN = 80
_HOUR = 3600.0
_WS = re.compile(r"\s+")
_SESSION_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_LATIN = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")

try:
    from biocore_model import boundary_gate as _boundary_gate
except Exception:  # pragma: no cover - only when the plugin root is absent
    _boundary_gate = None

try:
    from biocore_model import intero_core as _intero_core
except Exception:  # pragma: no cover
    _intero_core = None

try:
    from biocore_runtime import rerank as _rerank_mod
except Exception:  # pragma: no cover
    _rerank_mod = None

try:  # task-221: one global core state shared by every session
    from . import core_state as _core_state
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import core_state as _core_state
    except Exception:
        _core_state = None

try:  # task-223: memory governance + relationship accumulation
    from . import governance as _governance
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import governance as _governance
    except Exception:
        _governance = None
try:
    from . import relationship_state as _relationship_state
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import relationship_state as _relationship_state
    except Exception:
        _relationship_state = None
try:  # task-62: normalized channels / chunks / shards / prediction
    from . import retrieval_plus as _retrieval_plus
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import retrieval_plus as _retrieval_plus
    except Exception:
        _retrieval_plus = None
try:  # contract migration 1: cache stats in the core
    from . import cache_stats as _cache_stats
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import cache_stats as _cache_stats
    except Exception:
        _cache_stats = None
try:  # task-36 + task-78: tier hysteresis / reconsolidation / repair
    from . import lifecycle as _lifecycle
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import lifecycle as _lifecycle
    except Exception:
        _lifecycle = None
try:  # task-225: P0 memory + expression extensions
    from . import extensions as _extensions
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import extensions as _extensions
    except Exception:
        _extensions = None
try:  # task-224: P0 perception/relationship/self signals
    from . import signals as _signals
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import signals as _signals
    except Exception:
        _signals = None
try:  # task-222: self layer + autonomy
    from . import self_layer as _self_layer
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import self_layer as _self_layer
    except Exception:
        _self_layer = None
try:
    from . import autonomy as _autonomy
except Exception:  # pragma: no cover
    try:
        from biocore_runtime import autonomy as _autonomy
    except Exception:
        _autonomy = None

try:
    from biocore_model import sanitize as _sanitize
except Exception:  # pragma: no cover
    _sanitize = None

try:  # self-layer modules (all pure; optional so a stripped checkout still runs)
    from biocore_model import x_directives as _x_directives
    from biocore_model import self_intent as _self_intent
    from biocore_model import value_order as _value_order
    from biocore_model import boundary as _boundary
    from biocore_model import secrecy as _secrecy
    from biocore_model import narrative as _narrative
    from biocore_model import dissonance as _dissonance
    from biocore_model import attributor as _attributor
except Exception:  # pragma: no cover
    _x_directives = _self_intent = _value_order = None
    _boundary = _secrecy = _narrative = _dissonance = _attributor = None


# ---------------------------------------------------------------- utilities
def clean_text(value, limit=MAX_TEXT_CHARS):
    text = _WS.sub(" ", str(value or "")).strip()
    return text[:max(0, int(limit))]


def sanitize_session_id(session_id):
    raw = str(session_id or "").strip()[:SESSION_MAX_LEN]
    raw = _SESSION_SAFE.sub("_", raw).strip("._-")
    return raw or "default"


def _as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off", ""):
            return False
    return bool(default)


def _num(value, default=0.0):
    try:
        number = float(value)
    except Exception:
        return float(default)
    return number if math.isfinite(number) else float(default)


def _clamp(value, low, high):
    return max(low, min(high, value))


def _int_param(value, default, low, high):
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(low, min(high, number))


SWITCH_KEYS = ("safety_enabled", "boundary_enabled",
               "crisis_safety_enabled", "proactive_send")
SWITCH_DEFAULTS = {key: False for key in SWITCH_KEYS}
_SWITCH_ALIASES = {"proactive_autonomy": "proactive_send"}


def normalize_switches(value):
    """Four switches, all default false; proactive_autonomy aliases proactive_send."""
    data = value if isinstance(value, dict) else {}
    out = dict(SWITCH_DEFAULTS)
    for key in SWITCH_KEYS:
        if key in data:
            out[key] = _as_bool(data.get(key), False)
    if "proactive_send" not in data and "proactive_autonomy" in data:
        out["proactive_send"] = _as_bool(data.get("proactive_autonomy"), False)
    return out


def _default_store():
    env = str(os.environ.get("BIOCORE_MEMORY_STORE") or "").strip()
    if env:
        return env
    return str(Path(_PLUGIN_ROOT) / "data" / "memory")


def _store_root(store):
    if isinstance(store, dict):
        root = store.get("root") or store.get("store") or store.get("path")
        return Path(str(root)) if root else None
    if isinstance(store, (str, os.PathLike)):
        text = str(store or "").strip()
        return Path(text) if text else None
    return None


# ---------------------------------------------------------------- storage
def session_dir(store, session_id):
    root = _store_root(store)
    if root is None:
        return None
    return root / "sessions" / sanitize_session_id(session_id)


def memory_path(store, session_id):
    directory = session_dir(store, session_id)
    return (directory / "memory.json") if directory is not None else None


def capture_state_path(store, session_id):
    directory = session_dir(store, session_id)
    return (directory / "capture_state.json") if directory is not None else None


def _empty_doc(session_id):
    return {"schema_version": 1, "session_id": sanitize_session_id(session_id),
            "entries": []}


def _load_json(path, fallback):
    try:
        if path is None or not Path(path).exists():
            return fallback
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def load_memory(store, session_id):
    root = _store_root(store)
    if root is None:
        return {"entries": []}
    doc = _load_json(memory_path(store, session_id), _empty_doc(session_id))
    entries = doc.get("entries") if isinstance(doc.get("entries"), list) else []
    doc["entries"] = entries
    doc.setdefault("session_id", sanitize_session_id(session_id))
    return doc


def save_memory(store, session_id, doc):
    if _store_root(store) is None:
        return False
    try:
        _atomic_write(memory_path(store, session_id), doc)
        return True
    except Exception:
        return False


def load_capture_state(store, session_id):
    if _store_root(store) is None:
        return {}
    data = _load_json(capture_state_path(store, session_id), {})
    return data if isinstance(data, dict) else {}


def save_capture_state(store, session_id, state):
    if _store_root(store) is None:
        return False
    try:
        _atomic_write(capture_state_path(store, session_id), state)
        return True
    except Exception:
        return False


def _visible(entries):
    return [entry for entry in (entries or [])
            if isinstance(entry, dict) and not entry.get("deleted")
            and not entry.get("archived")]


def _compact(entry):
    if not isinstance(entry, dict):
        return {}
    return {key: entry.get(key) for key in
            ("id", "text", "role", "ts", "tags", "pin", "importance", "hits")
            if key in entry}


# ---------------------------------------------------------------- capture
def _stamps_for(state, session_id):
    hourly = state.get("hourly")
    if isinstance(hourly, dict):
        hourly = hourly.get(sanitize_session_id(session_id), [])
    values = []
    for value in hourly if isinstance(hourly, list) else []:
        stamp = _num(value, -1.0)
        if stamp >= 0:
            values.append(stamp)
    return values


def _recent_for(state, session_id):
    recent = state.get("recent")
    if isinstance(recent, dict):
        recent = recent.get(sanitize_session_id(session_id), [])
    return [str(item) for item in recent] if isinstance(recent, list) else []


def capture_decision(session_id, role, text, state):
    """Pure per-message capture decision (no mutation, no IO)."""
    state = state if isinstance(state, dict) else {}
    role = str(role or "user").strip().lower()
    text = clean_text(text, _int_param(state.get("max_text_chars"),
                                       MAX_TEXT_CHARS, 1, 20000))
    decision = {"capture": False, "reason": "", "role": role, "text": text}
    if not _as_bool(state.get("enabled", True), True):
        decision["reason"] = "disabled"
        return decision
    if role not in ("user", "assistant"):
        decision["reason"] = "bad-role"
        return decision
    if not text:
        decision["reason"] = "empty"
        return decision
    if len(text) < _int_param(state.get("min_chars"), MIN_CHARS, 2, 200):
        decision["reason"] = "too-short"
        return decision
    if text in _recent_for(state, session_id):
        decision["reason"] = "duplicate"
        return decision
    now = _num(state.get("now"), time.time())
    hourly_cap = _int_param(state.get("hourly_cap"), HOURLY_CAP, 0, 1000)
    live = [stamp for stamp in _stamps_for(state, session_id)
            if now - stamp < _HOUR]
    if hourly_cap <= 0 or len(live) >= hourly_cap:
        decision["reason"] = "hourly-limit"
        return decision
    decision["capture"] = True
    decision["reason"] = "ok"
    return decision


def capture(session_id, role, text, store, config=None):
    """Decide + remember + persist per-session capture state (thin IO)."""
    cfg = config if isinstance(config, dict) else {}
    session_id = sanitize_session_id(session_id)
    store = store or _default_store()
    now = _num(cfg.get("now"), time.time())
    state = load_capture_state(store, session_id)
    state.update({
        "now": now,
        "min_chars": cfg.get("min_chars", state.get("min_chars", MIN_CHARS)),
        "hourly_cap": cfg.get("hourly_cap", state.get("hourly_cap", HOURLY_CAP)),
        "max_text_chars": cfg.get("max_text_chars",
                                  state.get("max_text_chars", MAX_TEXT_CHARS)),
        "enabled": cfg.get("enabled", state.get("enabled", True)),
    })
    decision = capture_decision(session_id, role, text, state)
    entry = None
    if decision["capture"]:
        result = tool_call("remember", {
            "text": decision["text"], "role": decision["role"], "ts": now,
        }, store, session_id=session_id)
        entry = result.get("entry") if result.get("ok") else None
        if entry is not None:
            hourly = _stamps_for(state, session_id) + [now]
            recent = _recent_for(state, session_id) + [decision["text"]]
            save_capture_state(store, session_id, {
                "session_id": session_id,
                "hourly": hourly[-MAX_ENTRIES:],
                "recent": recent[-50:],
                "updated_ts": now,
            })
    return {"ok": True, "decision": decision, "entry": entry}


# ---------------------------------------------------------------- governance
def _concepts_of(text):
    if _governance is None:
        return []
    try:
        return _governance.extract_concepts(text)
    except Exception:
        return []


def _concept_filter(entries, query):
    """Keep entries whose concepts intersect the query (fallback: all)."""
    rows = [entry for entry in (entries or []) if isinstance(entry, dict)]
    wanted = set(_concepts_of(query))
    if not wanted:
        return rows
    narrowed = [entry for entry in rows
                if wanted & set(entry.get("concepts") or [])]
    return narrowed or rows


def _signals_eval(store, session_id, user_text="", assistant_text="",
                  state=None, now=None):
    if _signals is None or not store:
        return None
    try:
        rel = (relationship_read(store)["relationship"]
               if _store_root(store) is not None else {})
        return _signals.evaluate(store, session_id=session_id,
                                 user_text=user_text,
                                 assistant_text=assistant_text,
                                 state=state or {}, relationship=rel, now=now)
    except Exception:
        return None


def _governance_tick(store, now=None):
    """Decay/index/graph/aggregate sidecars; never deletes a memory."""
    if _governance is None or not store:
        return None
    try:
        return _governance.run(store, now=now)
    except Exception:
        return None


def _relationship_tick(store, user_text, now=None):
    """Bounded trust/closeness/conflict accumulation + attachment/repair."""
    if _relationship_state is None or not store:
        return None
    try:
        if not _relationship_state.enabled(store):
            return {"enabled": False, "skipped": True}
        before = relationship_read(store)["relationship"]
        signal = _relationship_state.signals(user_text)
        deltas = signal.get("deltas") or {}
        if deltas:
            relationship_update(store, deltas=deltas, now=now)
        after = relationship_read(store)["relationship"]
        summary = _relationship_state.summary(store, after)
        style = summary.get("attachment_style")
        if style and style != after.get("attachment_style"):
            relationship_update(store, attachment_style=style, now=now)
            after = relationship_read(store)["relationship"]
        _relationship_state.record_event(store, signal.get("kind"),
                                         user_text, deltas, now=now)
        return {"kind": signal.get("kind"), "deltas": deltas,
                "trust": round(_num(after.get("trust"), 0.5), 4),
                "closeness": round(_num(after.get("closeness"), 0.5), 4),
                "conflict": round(_num(after.get("conflict"), 0.0), 4),
                "delta_trust": round(_num(after.get("trust"), 0.5)
                                     - _num(before.get("trust"), 0.5), 4),
                "delta_closeness": round(_num(after.get("closeness"), 0.5)
                                         - _num(before.get("closeness"), 0.5), 4),
                "attachment_style": str(after.get("attachment_style") or style),
                "summary": summary}
    except Exception:
        return None


def _identity_tick(store, narrative=None, now=None):
    if _self_layer is None or not store:
        return None
    try:
        cfg = _load_runtime_config(store)
        persona_hash = _self_layer.hash_persona(cfg.get("persona_text"))
        return _self_layer.update(store, persona_hash=persona_hash,
                                  narrative=narrative, now=now)
    except Exception:
        return None


def _autonomy_tick(store, data=None, now=None):
    if _autonomy is None or not store:
        return None
    try:
        payload = data if isinstance(data, dict) else {}
        switches = _effective_switches(store, payload.get("switches"))
        if not switches.get("core_enabled", True) or not switches.get(
                "autonomy", True):
            return {"ok": True, "action": None, "reason": "disabled",
                    "metrics": _autonomy.metrics(store, now),
                    "limits": _autonomy.limits(store)}
        core_path = _core_state_path(payload)
        state = {}
        if _core_state is not None and _core_state.exists(core_path):
            doc = _core_state.load(core_path)
            state = {"core": _core_state.core_of(doc),
                     "intero": doc.get("intero")}
        elif isinstance(payload.get("state"), dict):
            state = payload["state"]
        return _autonomy.decide(
            store, now=now,
            session_id=str(payload.get("session_id") or "autonomy"),
            state=state,
            last_human_ts=_num(payload.get("last_human_ts"), 0.0),
            silence_minutes=_num(payload.get("silence_minutes"), 0.0))
    except Exception:
        return None


# ---------------------------------------------------------------- inject
def _store_data_dir(store):
    root = _store_root(store)
    if root is None:
        return None
    return root.parent if root.name == "memory" else root


def _data_flags(store):
    """<dataDir>/flags.json booleans (learning switches live next to memory/).

    task-221口径: DSH learning switches default ON; an explicit file value wins.
    """
    merged = {"mood_recall": True, "affect_encoding": True,
              "memory_feedback": True, "preference_drift": True}
    data_dir = _store_data_dir(store)
    if data_dir is None:
        return merged
    raw = _load_json(data_dir / "flags.json", {})
    for key, value in raw.items():
        if isinstance(value, bool):
            merged[str(key)] = value
    cfg = _load_runtime_config(store)
    switches = cfg.get("switches") if isinstance(cfg.get("switches"), dict) else {}
    for key, value in switches.items():
        if isinstance(value, bool):
            merged[str(key)] = value
    return merged


def _inject_engine(store, entries):
    """MemoryEngine over this session's entries, with the env embedder."""
    try:
        from biocore_memory.engine import MemoryEngine
        from biocore_memory.recall.embedding import load_provider
        from biocore_memory.store.json_store import MemoryStore
    except Exception:
        return None
    embedder = None
    try:
        cfg = _load_runtime_config(store)
        emb = cfg.get("embedding") if isinstance(cfg.get("embedding"), dict) else {}
        provider_cfg = {}
        if emb:
            provider_cfg = {
                "embedding_mode": emb.get("mode") or "auto",
                "embedding_base_url": emb.get("base_url") or "",
                "embedding_model": emb.get("model") or "",
                "embedding_api_key": emb.get("api_key") or "",
            }
        embedder = load_provider(provider_cfg)
    except Exception:
        embedder = None
    try:
        memory = MemoryStore(doc={"entries": [dict(item) for item in entries
                                              if isinstance(item, dict)]})
        return MemoryEngine(store=memory, embedder=embedder,
                            config={"flags": _data_flags(store)})
    except Exception:
        return None


def _rerank_entries(query, entries, rerank_config):
    """Reorder candidates by the optional rerank API; None keeps local order."""
    if _rerank_mod is None or not isinstance(rerank_config, dict):
        return None
    if not rerank_config.get("rerank_enabled"):
        return None
    try:
        texts = [str(entry.get("text") or "") for entry in entries]
        ranked = _rerank_mod.rerank_documents(query, texts, rerank_config)
    except Exception:
        return None
    if not ranked:
        return None
    ordered = []
    for item in ranked:
        try:
            index = int(item.get("index", -1))
        except Exception:
            continue
        if 0 <= index < len(entries) and entries[index] not in ordered:
            ordered.append(entries[index])
    for entry in entries:
        if entry not in ordered:
            ordered.append(entry)
    return ordered or None


def _query_entries(store, session_id, query, top_k, rerank_config):
    """engine.recall(query) over the injected candidates; None means fallback."""
    entries = _visible(load_memory(store, session_id).get("entries"))
    if not entries:
        return None
    engine = _inject_engine(store, entries)
    if engine is None:
        return None
    rows = engine.recall(str(query)[:MAX_TEXT_CHARS], budget=top_k)
    if not isinstance(rows, list) or not rows:
        return None
    picked = [row.get("entry") for row in rows
              if isinstance(row, dict) and isinstance(row.get("entry"), dict)]
    if not picked:
        return None
    # task-223: concept filter (entries written with concepts only).
    narrowed = _concept_filter(picked, query)
    if 0 < len(narrowed) < len(picked):
        picked = narrowed
        if _governance is not None:
            try:
                _governance.note_concept_filter(store, len(picked))
            except Exception:
                pass
    if len(picked) > 1:
        reordered = _rerank_entries(query, picked, rerank_config)
        if reordered:
            picked = reordered
    # task-223: index hit + decay weight (cold memories sink, never deleted).
    if _governance is not None:
        try:
            hits = set(_governance.lookup(store, query))
            if hits:
                _governance.note_index_hit(store)
            if hits:
                picked.sort(key=lambda entry: (
                    1 if str(entry.get("id") or "") in hits else 0,
                    _governance.weight_of(store, str(entry.get("id") or "")),
                    _num(entry.get("importance"), 0.5)), reverse=True)
        except Exception:
            pass
    if _retrieval_plus is not None and query:
        try:
            text_query = str(query)
            switches = _effective_switches(store)
            enabled = bool(switches.get("retrieval_plus", True))
            _retrieval_plus.record_query(store, text_query, now=time.time())
            rerank_on = isinstance(rerank_config, dict) and bool(
                rerank_config.get("rerank_enabled")
                or rerank_config.get("enabled"))
            if enabled and not rerank_on and len(picked) > 1:
                ranked = _retrieval_plus.rank(
                    store, session_id, text_query, picked,
                    now=time.time(), limit=len(picked))
                if ranked:
                    _retrieval_plus.record_debug(store, text_query, ranked)
                    engine_scores = {str(row.get("entry", {}).get("id") or ""):
                                     _num(row.get("score"), 0.0) for row in rows}
                    top = max(engine_scores.values() or [1.0]) or 1.0
                    combined = []
                    for item in ranked:
                        entry_id = str(item["entry"].get("id") or "")
                        engine_norm = engine_scores.get(entry_id, 0.0) / top
                        combined.append((0.7 * engine_norm + 0.3 * item["score"],
                                         item))
                    combined.sort(key=lambda pair: pair[0], reverse=True)
                    picked = [item["entry"] for _score, item in combined]
        except Exception:
            pass
    if _lifecycle is not None:
        try:
            _lifecycle.record_recall(store, session_id,
                                     [str(entry.get("id") or "")
                                      for entry in picked], now=time.time())
            _lifecycle.tier_tick(store, picked, now=time.time())
        except Exception:
            pass
    return picked[:top_k]


def inject_block(session_id, store, max_chars=MAX_CHARS, top_k=TOP_K,
                 query="", mode="query", rerank_config=None):
    """Top-k session memories as one bounded Chinese prompt block.

    mode="query"  recall this turn's words (engine.recall + env embedder,
                  optional rerank); any failure falls back to recent.
    mode="recent" keep the pin+ts ordering.
    """
    session_id = sanitize_session_id(session_id)
    budget = _int_param(max_chars, MAX_CHARS, 0, MAX_CHARS)
    limit = _int_param(top_k, TOP_K, 1, INJECT_MAX_K)
    text_query = clean_text(query, MAX_TEXT_CHARS)
    chosen = str(mode or "query").strip().lower()
    entries = []
    query_used = False
    if chosen == "query" and text_query:
        try:
            picked = _query_entries(store, session_id, text_query, limit,
                                    rerank_config)
        except Exception:
            picked = None
        if picked:
            entries = list(picked)
            query_used = True
    if not entries:
        entries = sorted(_visible(load_memory(store, session_id).get("entries")),
                         key=lambda entry: (1 if entry.get("pin") else 0,
                                            _num(entry.get("ts"), 0.0)),
                         reverse=True)[:limit]
    if (query_used and text_query and entries and _extensions is not None):
        try:
            all_entries = _visible(load_memory(store, session_id).get("entries"))
            ext = _extensions.expand_recall(
                store, all_entries, text_query,
                [str(entry.get("id") or "") for entry in entries])
            for row in ext.get("extra") or []:
                if row not in entries:
                    entries.append(row)
            entries = entries[:max(limit, min(limit + 2, INJECT_MAX_K))]
        except Exception:
            pass
    header = "你记得这些事："
    ids = []
    if not entries or budget <= len(header) + 3:
        return {"text": "", "chars": 0, "ids": []}
    text = header
    for entry in entries:
        content = clean_text(entry.get("text"), 500)
        if not content:
            continue
        room = budget - len(text)
        if room <= 2:
            break
        line = "\n- " + content
        if len(line) > room:
            clipped = room - 4
            if clipped <= 1:
                break
            line = "\n- " + content[:clipped] + "…"
        text += line
        ids.append(str(entry.get("id") or ""))
    if len(text) <= len(header) + 2:
        return {"text": "", "chars": 0, "ids": []}
    return {"text": text, "chars": len(text), "ids": ids}


# ---------------------------------------------------------------- gate
def gate_reply(reply_text, flags=None):
    """Expression gate using the same rules as the AstrBot boundary path."""
    data = flags if isinstance(flags, dict) else {}
    raw_switches = data.get("switches") if isinstance(data.get("switches"), dict) else data
    effective = normalize_switches(raw_switches)
    user_text = str(data.get("user_text") or "")
    text = str(reply_text or "")
    if _boundary_gate is None:
        return {"ok": True, "hit": False, "blocked": False, "action": "pass",
                "rule": "", "label": "", "reason": "gate_unavailable",
                "text": text, "changed": False, "rules": []}
    decision = _boundary_gate.evaluate(text, user_text=user_text,
                                       flags=effective)
    blocked = bool(decision.get("blocked"))
    rule = str(decision.get("rule") or "")
    final = str(decision.get("text") or text)
    if not blocked:
        action = "pass"
    elif rule == "crisis_safety":
        action = "replace"
    else:
        action = "truncate"
    return {
        "ok": True,
        "hit": blocked,
        "blocked": blocked,
        "action": action,
        "rule": rule,
        "label": str(decision.get("label") or rule),
        "reason": str(decision.get("label") or rule),
        "text": final,
        "changed": bool(decision.get("changed")),
        "rules": list(decision.get("rules") or []),
    }


# ---------------------------------------------------------------- prompt block
def _state_block(state, budget):
    state = state if isinstance(state, dict) else {}
    core = state.get("core") if isinstance(state.get("core"), dict) else {}
    intero = state.get("intero") if isinstance(state.get("intero"), dict) else {}
    if not intero and isinstance(core.get("intero"), dict):
        intero = core["intero"]
    relation = state.get("relation") if isinstance(state.get("relation"), dict) else {}
    tone = state.get("tone") if isinstance(state.get("tone"), dict) else {}
    autonomy = state.get("autonomy") if isinstance(state.get("autonomy"), dict) else {}
    v = _num(state.get("v", core.get("v", state.get("valence"))), 0.0)
    parts = ["心情不错" if v >= 0.35 else ("心情有点低" if v <= -0.35 else "心情平稳")]
    for key, high, low in (
        ("tension", "绷得有点紧", "还算松"),
        ("warmth", "想靠近一点", None),
        ("heaviness", "有点沉", None),
        ("clarity", None, "没听太清"),
    ):
        value = _num(intero.get(key), 0.5)
        if high and value >= 0.62:
            parts.append(high)
        if low and value <= 0.38:
            parts.append(low)
    label = str(tone.get("label") or "").strip()
    if label:
        parts.append(label)
    stage = str(relation.get("stage") or "").strip()
    if stage:
        parts.append("关系" + stage)
    autonomy_state = str(autonomy.get("state") or "").strip()
    if autonomy_state and autonomy_state not in ("静", ""):
        parts.append("自主" + autonomy_state)
    text = "此刻：" + "；".join(parts) + "。"
    if len(text) > budget:
        text = text[:max(1, budget - 1)] + "…"
    return text


def _constraints_block(state, switches, payload):
    state = state if isinstance(state, dict) else {}
    switches = switches if isinstance(switches, dict) else {}
    core = state.get("core") if isinstance(state.get("core"), dict) else {}
    intero = state.get("intero") if isinstance(state.get("intero"), dict) else {}
    if not intero and isinstance(core.get("intero"), dict):
        intero = core["intero"]
    tension = _num(intero.get("tension"), 0.5)
    v = _num(state.get("v", core.get("v", state.get("valence"))), 0.0)
    lines = []
    if v <= -0.3 or tension >= 0.65:
        lines.append("先接住情绪再谈事，句子短一点、语气放软。")
    else:
        lines.append("语气自然平稳，先回应对方在意的事。")
    if _as_bool(payload.get("length_hint"), False):
        target = _int_param(payload.get("length_target"), 10, 1, 30)
        hard = _int_param(payload.get("length_max"), 30, target, 60)
        lines.append("一次只说一句，通常 %d 字以内，最多 %d 字；需要停顿用换行。"
                     % (target, hard))
    if _as_bool(switches.get("proactive_send"), False):
        lines.append("可以主动一点，但不连发、不催促。")
    else:
        lines.append("先不主动发新消息，等对方开口。")
    allow = _as_bool(payload.get("allow_correction"), False)
    lines.append("允许改口：" + ("是，说错了可以补一句。" if allow else "否，先确认再答。"))
    return "\n".join("- " + line for line in lines[:6])


def prompt_block(payload):
    """State block (<=200 chars) + short expression constraints (task-204)."""
    data = payload if isinstance(payload, dict) else {}
    phase = str(data.get("phase") or "turn").strip().lower()
    if phase not in ("boot", "turn"):
        phase = "turn"
    budget = _int_param(data.get("max_chars"), MAX_STATE_CHARS, 40,
                        MAX_STATE_CHARS)
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    switches = normalize_switches(data.get("switches"))
    state_text = _state_block(state, budget)
    constraints = _constraints_block(state, switches, data)
    text = (state_text + "\n" + constraints).strip()
    if _lifecycle is not None:
        try:
            repair = _lifecycle.repair_lines(data.get("store"))
            if repair:
                text = text + "\n" + "\n".join("- " + line for line in repair)
        except Exception:
            pass
    expression = None
    if _extensions is not None:
        try:
            target = _int_param(data.get("length_target"), 0, 0, 400)
            hard = _int_param(data.get("length_max"), 0, 0, 400)
            expression = _extensions.expression_block(
                data.get("store"), state, data.get("relationship"),
                soft_target=target, soft_max=hard)
            extra = [value for value in (expression.get("rules"),
                                         expression.get("template"),
                                         expression.get("topic"),
                                         expression.get("voice")) if value]
            lines = []
            for value in extra:
                for line in str(value).splitlines():
                    line = line.strip()
                    if line:
                        lines.append(line if line.startswith("- ") else "- " + line)
            if lines:
                text = text + "\n" + "\n".join(lines[:6])
        except Exception:
            expression = None
    thinking_line = thinking_style_line(data)
    persona_line = persona_prompt_line(data)
    if thinking_line:
        text = text + "\n" + thinking_line
    if persona_line:
        text = text + "\n" + persona_line
    sections = {"state": state_text, "constraints": constraints}
    if thinking_line:
        sections["thinking_style"] = thinking_line
    if expression:
        sections["expression"] = expression
    if persona_line:
        sections["persona"] = persona_line
    return {"ok": True, "phase": phase, "text": text, "chars": len(text),
            "sections": sections}


# ---------------------------------------------------------------- constraints
def constraint_hint(query, open_loops=None, commitments=None):
    """Same <=140-char hint as the existing AstrBot constraints path (thin)."""
    try:
        from biocore_memory.recall.constraints import check_constraints
        return check_constraints(query, open_loops=open_loops,
                                 commitments=commitments)
    except Exception:
        return None


# ---------------------------------------------------------------- tools
def _tokens(text):
    lowered = str(text or "").lower()
    out = _LATIN.findall(lowered)
    for run in _CJK.findall(lowered):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[index:index + 2] for index in range(len(run) - 1))
    return out


def _score(query, text):
    q_tokens = set(_tokens(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(_tokens(text))
    if not t_tokens:
        return 0.0
    overlap = len(q_tokens & t_tokens)
    if not overlap:
        return 1.0 if str(query) and str(query) in str(text) else 0.0
    return round(overlap / float(len(q_tokens)), 4)


def _new_entry(text, role, tags, now):
    base = "%s|%s|%s" % (role, text, now)
    return {
        "id": hashlib.sha1(base.encode("utf-8")).hexdigest()[:12],
        "text": text,
        "role": role,
        "ts": round(float(now), 3),
        "tags": [str(tag)[:32] for tag in (tags or []) if str(tag).strip()],
        "pin": False,
        "importance": 0.5,
        "hits": 1,
        "concepts": _concepts_of(text),
    }


def tool_call(name, args, store, session_id=None):
    """remember / recall / forget against one session store."""
    name = str(name or "").strip()
    if name in ("biocore_remember", "remember"):
        name = "remember"
    elif name in ("biocore_recall", "recall"):
        name = "recall"
    elif name in ("biocore_forget", "forget"):
        name = "forget"
    else:
        return {"ok": False, "reason": "unknown_tool"}
    args = args if isinstance(args, dict) else {}
    sid = sanitize_session_id(session_id or args.get("session_id") or "default")
    store = store or _default_store()
    doc = load_memory(store, sid)
    entries = doc.setdefault("entries", [])
    if name == "remember":
        text = clean_text(args.get("text"))
        role = str(args.get("role") or "user").strip().lower()
        if not text:
            return {"ok": False, "reason": "text_required"}
        if role not in ("user", "assistant"):
            role = "user"
        for existing in entries:
            if (str(existing.get("text") or "") == text
                    and str(existing.get("role") or "user") == role):
                existing["hits"] = int(existing.get("hits", 1) or 1) + 1
                save_memory(store, sid, doc)
                return {"ok": True, "entry": _compact(existing), "deduped": True}
        now = _num(args.get("ts"), time.time())
        entry = _new_entry(text, role, args.get("tags"), now)
        entries.append(entry)
        if len(entries) > MAX_ENTRIES:
            doc["entries"] = entries[-MAX_ENTRIES:]
        save_memory(store, sid, doc)
        return {"ok": True, "entry": _compact(entry), "deduped": False}
    if name == "recall":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "reason": "query_required"}
        limit = _int_param(args.get("limit"), 3, 1, 10)
        rows = []
        for entry in _visible(entries):
            score = _score(query, entry.get("text"))
            if score <= 0:
                continue
            row = _compact(entry)
            row["score"] = score
            rows.append(row)
        rows.sort(key=lambda row: (-row["score"], -_num(row.get("ts"), 0.0)))
        rerank_config = args.get("rerank_config")
        if (_rerank_mod is not None and isinstance(rerank_config, dict)
                and rerank_config.get("rerank_enabled")):
            try:
                reranked = _rerank_mod.rerank_documents(
                    query, [row.get("text", "") for row in rows], rerank_config)
                if reranked:
                    seen = set()
                    ordered = []
                    for item in reranked:
                        index = int(item.get("index", -1))
                        if 0 <= index < len(rows) and index not in seen:
                            seen.add(index)
                            ordered.append(rows[index])
                    for index, row in enumerate(rows):
                        if index not in seen:
                            ordered.append(row)
                    rows = ordered
            except Exception:
                pass  # rerank is optional; recall keeps the local order
        return {"ok": True, "count": min(limit, len(rows)),
                "results": rows[:limit]}
    target = str(args.get("id") or args.get("query") or "").strip()
    if not target:
        return {"ok": False, "reason": "id_or_query_required"}
    removed = []
    for entry in list(entries):
        if str(entry.get("id") or "") == target:
            removed.append(entry)
            entries.remove(entry)
            break
    if not removed:
        rows = tool_call("recall", {"query": target, "limit": 1}, store,
                         session_id=sid).get("results") or []
        if rows:
            target_id = str(rows[0].get("id") or "")
            for entry in list(entries):
                if str(entry.get("id") or "") == target_id:
                    removed.append(entry)
                    entries.remove(entry)
                    break
    if not removed:
        return {"ok": False, "reason": "not_found"}
    save_memory(store, sid, doc)
    return {"ok": True, "id": str(removed[0].get("id") or ""),
            "removed": True}


# ---------------------------------------------------------------- proactive
# task-205 follow-up: the proactive DECISION lives here; Node only executes it.
PROACTIVE_MIN_INTERVAL = 5
PROACTIVE_MAX_INTERVAL = 240
PROACTIVE_DEFAULT_MIN_GAP = 180
PROACTIVE_DEFAULT_PER_HOUR = 2
PROACTIVE_DEFAULT_MIN_TRUST = 0.6
PROACTIVE_DEFAULT_MAX_CHARS = 60
_COMMITMENT_CUES = ("明天", "后天", "下周", "下个月", "回头", "改天", "记得",
                    "答应", "约好", "体检", "报告", "考试", "面试", "见面",
                    "打算", "要去做")
_DUE_SOON = ("明天", "后天", "下周")


def _ms(value, fallback=0.0):
    """Normalize a timestamp to epoch milliseconds (accepts seconds or ms)."""
    number = _num(value, fallback)
    if number <= 0:
        return 0.0
    return number * 1000.0 if number < 1e12 else number


def _switch_value(switches, *names, default=False):
    data = switches if isinstance(switches, dict) else {}
    for name in names:
        if name in data:
            return _as_bool(data.get(name), default)
    return bool(default)


def _proactive_state_path(store):
    root = _store_root(store)
    return (root / "proactive.json") if root is not None else None


def _proactive_session_state(store, session_id, state_file=None):
    """Per-session dsh_adapter state: explicit file, else <store>/../sessions/<id>.json."""
    path = None
    if state_file:
        path = Path(str(state_file))
    else:
        root = _store_root(store)
        if root is not None:
            path = root.parent / "sessions" / (sanitize_session_id(session_id) + ".json")
    if path is None or not path.exists():
        return {}
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _proactive_commitments(store, session_id, limit=5):
    """Pending commitments from this session's memory texts (read-only)."""
    doc = load_memory(store, session_id)
    entries = [entry for entry in (doc.get("entries") or [])
               if isinstance(entry, dict) and not entry.get("deleted")
               and not entry.get("archived")]
    entries.sort(key=lambda entry: _num(entry.get("ts"), 0.0), reverse=True)
    loops = []
    seen = set()
    for entry in entries[:max(limit * 6, 12)]:
        text = clean_text(entry.get("text"), 120)
        if not text or not any(cue in text for cue in _COMMITMENT_CUES):
            continue
        key = text[:40]
        if key in seen:
            continue
        seen.add(key)
        due = "soon" if any(cue in text for cue in _DUE_SOON) else ""
        loops.append({"key": "p%d" % (len(loops) + 1), "title": key,
                      "status": "open", "due_hint": due})
        if len(loops) >= limit:
            break
    return loops


def _proactive_pick(loops):
    rows = [row for row in (loops or [])
            if isinstance(row, dict) and row.get("status") != "closed"]
    due = [row for row in rows
           if str(row.get("due_hint") or "") in ("soon", "tomorrow")]
    return (due or rows or [None])[0]


def _proactive_compose(loop, max_chars=PROACTIVE_DEFAULT_MAX_CHARS):
    title = clean_text((loop or {}).get("title") or (loop or {}).get("text")
                       or (loop or {}).get("key"), 30)
    text = ("你之前提过「" + title + "」，后来怎么样了？"
            if title else "在忙吗？突然想问你一句。")
    cap = _int_param(max_chars, PROACTIVE_DEFAULT_MAX_CHARS, 10, 120)
    return text if len(text) <= cap else text[:cap - 1] + "…"


def proactive_tick(session_id, last_human_ts=None, switches=None, store=None,
                   now=None, state_file=None):
    """One proactive decision tick (policy only; the caller performs delivery).

    Inputs: session_id, last_human_ts, switches, store (memory store root),
    optional now / state_file. Output:
      {ok, should_send, text, reason, gap_minutes}
    Cooldown state is booked here only when should_send is true; Node then
    only delivers the returned text (and nothing else).
    """
    sid = sanitize_session_id(session_id)
    data = switches if isinstance(switches, dict) else {}
    enabled = _switch_value(data, "proactiveEnabled", "proactive_enabled",
                            default=False)
    send = _switch_value(data, "proactiveSend", "proactive_send",
                         default=False)
    now_ms = _ms(now, time.time() * 1000.0) or time.time() * 1000.0
    last_ms = _ms(last_human_ts, 0.0)
    gap_minutes = round(max(0.0, now_ms - last_ms) / 60000.0, 1) if last_ms > 0 else 0.0
    result = {"ok": True, "should_send": False, "text": "",
              "reason": "", "gap_minutes": gap_minutes}
    if not enabled:
        result["reason"] = "disabled"
        return result
    if not send:
        result["reason"] = "send_off"
        return result
    if not str(session_id or "").strip():
        result["reason"] = "no_session"
        return result

    min_gap = _int_param(data.get("proactiveMinGapMinutes",
                                  data.get("proactive_min_gap_minutes")),
                         PROACTIVE_DEFAULT_MIN_GAP, 10, 48 * 60)
    if last_ms <= 0 or gap_minutes < min_gap:
        result["reason"] = "too_soon"
        return result

    per_hour = _int_param(data.get("proactivePerHour",
                                   data.get("proactive_per_hour")),
                          PROACTIVE_DEFAULT_PER_HOUR, 1, 6)
    min_trust = _num(data.get("proactiveMinTrust",
                              data.get("proactive_min_trust")),
                     PROACTIVE_DEFAULT_MIN_TRUST)
    max_chars = _int_param(data.get("proactiveMaxChars",
                                    data.get("proactive_max_chars")),
                           PROACTIVE_DEFAULT_MAX_CHARS, 10, 120)
    require_loop = _as_bool(data.get(
        "proactiveRequireOpenLoop",
        data.get("proactive_require_open_loop", True)), True)

    state_path = _proactive_state_path(store)
    persist = _load_json(state_path, {}) if state_path is not None else {}
    sent = []
    for item in persist.get("sent") or []:
        value = _ms(item, 0.0)
        if value > 0:
            sent.append(value)
    sent = [stamp for stamp in sent if now_ms - stamp < _HOUR * 1000.0]
    if len(sent) >= per_hour:
        result["reason"] = "hourly_cap"
        return result
    last_sent_ms = _ms(persist.get("lastSentAt"), 0.0)
    if last_sent_ms > 0 and now_ms - last_sent_ms < min_gap * 60000.0:
        result["reason"] = "cooldown"
        return result

    session_state = _proactive_session_state(store, sid, state_file=state_file)
    rel = session_state.get("relationship") if isinstance(
        session_state.get("relationship"), dict) else {}
    trust = _num(rel.get("trust"), 0.5)
    if trust < min_trust:
        result["reason"] = "not_close"
        result["trust"] = round(trust, 4)
        return result

    loops = _proactive_commitments(store, sid)
    loop = _proactive_pick(loops)
    if require_loop and loop is None:
        result["reason"] = "no_commitment"
        return result

    text = _proactive_compose(loop, max_chars)
    result.update({"should_send": True, "text": text, "reason": "ok",
                   "trust": round(trust, 4),
                   "loop": (loop or {}).get("title") or "",
                   "interval_minutes": _int_param(
                       data.get("proactiveIntervalMinutes",
                                data.get("proactive_interval_minutes")),
                       30, PROACTIVE_MIN_INTERVAL, PROACTIVE_MAX_INTERVAL)})
    if state_path is not None:
        _atomic_write(state_path, {"lastSentAt": now_ms,
                                   "sent": (sent + [now_ms])[-24:],
                                   "lastText": text[:80], "ts": now_ms})
    return result


# ---------------------------------------------------------------- self layer
def _module_flags(payload, name):
    """Per-module switch from payload.modules[name] (bool or {enabled,...})."""
    data = payload if isinstance(payload, dict) else {}
    modules = data.get("modules") if isinstance(data.get("modules"), dict) else {}
    entry = modules.get(name, True)
    if isinstance(entry, dict):
        flags = dict(entry)
        flags["enabled"] = _as_bool(entry.get("enabled", True), True)
        return flags
    return {"enabled": _as_bool(entry, True)}


def self_layer_hints(payload, switches=None):
    """Run the self-layer modules (missing module/data is skipped, never raises)."""
    data = payload if isinstance(payload, dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    relationship = (data.get("relationship")
                    if isinstance(data.get("relationship"), dict) else {})
    hooks = []
    if _self_intent is not None:
        flags = _module_flags(data, "self_intent")
        if flags["enabled"]:
            try:
                result = _self_intent.evaluate(
                    snapshot=state, relationship=relationship,
                    needs=data.get("needs"),
                    other_estimate=data.get("other_estimate"),
                    expected=data.get("expected"), flags=flags,
                    silence_seconds=_num(data.get("silence_seconds"), 0.0))
                hooks.append(("self_intent", _self_intent.directives(result)))
            except Exception:
                pass
    if _value_order is not None and isinstance(data.get("options"), list) and data.get("options"):
        flags = _module_flags(data, "value_order")
        if flags["enabled"]:
            try:
                context = data.get("value_context")
                context = context if isinstance(context, dict) else {}
                result = _value_order.rank(data.get("options"),
                                           weights=data.get("value_weights"),
                                           context=context, flags=flags)
                hooks.append(("value_order", _value_order.directives(result)))
            except Exception:
                pass
    if _boundary is not None:
        flags = _module_flags(data, "boundary")
        if flags["enabled"]:
            try:
                result = _boundary.judge(text=data.get("user_text"),
                                         relationship=relationship, flags=flags)
                if result.get("crossed"):
                    hooks.append(("boundary", {
                        "tone": "先守住边界",
                        "length_scale": 0.85,
                        "allow_correction": False,
                        "note": "底线：" + str(result.get("kind") or ""),
                        "sources": ["boundary"],
                    }))
            except Exception:
                pass
    if _secrecy is not None and isinstance(data.get("secrecy"), dict):
        flags = _module_flags(data, "secrecy")
        if flags["enabled"]:
            try:
                raw = data.get("secrecy") or {}
                decision = _secrecy.decide(
                    raw.get("sensitivity"), raw.get("relation"),
                    raw.get("setting_safety"))
                action = str(decision.get("action") or "")
                if action in ("withhold", "reframe"):
                    hooks.append(("secrecy", {
                        "tone": "有些话先不说",
                        "length_scale": 0.9,
                        "note": "秘密：" + action,
                        "sources": ["secrecy"],
                    }))
            except Exception:
                pass
    if _narrative is not None and isinstance(data.get("events"), list):
        flags = _module_flags(data, "narrative")
        if flags["enabled"]:
            try:
                result = _narrative.summarize(data.get("events"), flags=flags)
                hooks.append(("narrative", _narrative.directives(result)))
            except Exception:
                pass
    if _dissonance is not None and (data.get("stances") or data.get("behavior")):
        flags = _module_flags(data, "dissonance")
        if flags["enabled"]:
            try:
                result = _dissonance.strength(stances=data.get("stances"),
                                              behavior=data.get("behavior"),
                                              flags=flags)
                hooks.append(("dissonance", _dissonance.directives(result)))
            except Exception:
                pass
    if _attributor is not None and (data.get("dims") or data.get("event")):
        flags = _module_flags(data, "attributor")
        if flags["enabled"]:
            try:
                result = _attributor.attribute(dims=data.get("dims"),
                                               event=data.get("event"),
                                               relationship=relationship,
                                               flags=flags)
                hooks.append(("attributor", _attributor.directives(result)))
            except Exception:
                pass
    return hooks


def self_layer_constraints(payload, switches=None):
    """Six-key canonical constraints from the self layer (defaults all neutral)."""
    hooks = self_layer_hints(payload, switches=switches)
    if _x_directives is not None and hooks:
        return _x_directives.merge_named(hooks)
    return {"length_scale": 1.0, "initiative": 0.5, "tone": "",
            "allow_correction": False, "note": "", "sources": []}


# ---------------------------------------------------------------- interoception
def _intero_source(state):
    """Flatten core + relationship + body fields into one intero input dict."""
    state = state if isinstance(state, dict) else {}
    source = {}
    core = state.get("core") if isinstance(state.get("core"), dict) else {}
    source.update(core)
    relationship = state.get("relationship") if isinstance(state.get("relationship"), dict) else {}
    if relationship:
        for key in ("trust", "closeness", "conflict"):
            if key in relationship and key not in source:
                source[key] = relationship[key]
    nested = source.get("relationship_context")
    if isinstance(nested, dict):
        rel = nested.get("state") if isinstance(nested.get("state"), dict) else nested
        for key in ("trust", "closeness", "conflict"):
            if key in rel:
                source[key] = rel[key]
    for key in ("v", "a", "arousal", "stress", "energy", "social",
                "social_pain", "self_esteem", "trust", "closeness",
                "conflict", "sleep_debt", "allostatic_load", "sadness",
                "loneliness", "noise", "labels"):
        if key in state and key not in source:
            source[key] = state[key]
    if "conflict" not in source and "conflict_ema" in source:
        source["conflict"] = source["conflict_ema"]
    return source


def compute_intero(state, dt_hours=1.0, now=None):
    """Derive tension/warmth/heaviness/clarity from the current body state.

    Same pure intero_core formulas as the AstrBot bio_tick path: previous
    intero present -> intero_core.update(prev, source, dt); otherwise target.
    """
    state = state if isinstance(state, dict) else {}
    source = _intero_source(state)
    if _intero_core is None:  # stripped checkout fallback
        return {"tension": 0.3, "warmth": 0.6,
                "heaviness": 0.3, "clarity": 0.7}
    prev = None
    core = state.get("core") if isinstance(state.get("core"), dict) else {}
    if isinstance(core.get("intero"), dict):
        prev = core["intero"]
    elif isinstance(state.get("intero"), dict):
        prev = state["intero"]
    if isinstance(prev, dict) and any(key in prev for key in ("tension", "warmth",
                                                              "heaviness", "clarity")):
        return _intero_core.update(prev, source, dt_hours=dt_hours, now=now)
    return _intero_core.target(source)


def apply_intero_to_state(state, payload_state=None):
    """Compute intero from session+payload state, write state.intero/core.intero."""
    state = dict(state) if isinstance(state, dict) else {}
    merged = dict(state)
    if isinstance(payload_state, dict):
        if isinstance(payload_state.get("core"), dict):
            core = dict(merged.get("core")) if isinstance(merged.get("core"), dict) else {}
            core.update(payload_state["core"])
            merged["core"] = core
        for key, value in payload_state.items():
            if key == "core":
                continue
            if key not in merged:
                merged[key] = value
    intero = compute_intero(merged)
    merged["intero"] = intero
    core = dict(merged.get("core")) if isinstance(merged.get("core"), dict) else {}
    core["intero"] = intero
    merged["core"] = core
    return merged


# ---------------------------------------------------------------- shared relationship
RELATIONSHIP_FILE = "relationship.json"
RELATIONSHIP_BASE_FILE = "relationship_base.json"
RELATIONSHIP_FIELDS = ("trust", "closeness", "conflict")
RELATIONSHIP_DELTA_LIMITS = {"trust": 0.02, "closeness": 0.02, "conflict": 0.02}
ATTACHMENT_STYLES = ("secure", "anxious", "avoidant", "disorganized", "mixed")
LOCK_TIMEOUT_S = 3.0
LOCK_STALE_S = 10.0
DEFAULT_RELATIONSHIP = {
    "trust": 0.5,
    "closeness": 0.5,
    "conflict": 0.0,
    "attachment_style": "secure",
    "bids": {"made": 0, "answered": 0},
    "days_known": 0,
}


def relationship_path(store):
    root = _store_root(store)
    return (root / RELATIONSHIP_FILE) if root is not None else None


def state_path(store, session_id):
    directory = session_dir(store, session_id)
    return (directory / "state.json") if directory is not None else None


def _validated_relationship(raw):
    data = raw if isinstance(raw, dict) else {}
    out = {key: value for key, value in DEFAULT_RELATIONSHIP.items()}
    for key in RELATIONSHIP_FIELDS:
        out[key] = round(_clamp(_num(data.get(key), out[key]), 0.0, 1.0), 4)
    style = str(data.get("attachment_style") or out["attachment_style"]).strip().lower()
    out["attachment_style"] = style if style in ATTACHMENT_STYLES else out["attachment_style"]
    bids = data.get("bids") if isinstance(data.get("bids"), dict) else {}
    out["bids"] = {key: max(0, int(_num(bids.get(key), 0))) for key in ("made", "answered")}
    out["days_known"] = max(0, int(_num(data.get("days_known"), 0)))
    return out


def relationship_read(store):
    """Read <store>/relationship.json; missing/bad file -> defaults (never creates)."""
    path = relationship_path(store)
    if path is None:
        return {"relationship": dict(DEFAULT_RELATIONSHIP), "version": 0,
                "updated_ts": 0.0, "path": ""}
    raw = _load_json(path, {})
    rel = raw.get("relationship") if isinstance(raw.get("relationship"), dict) else raw
    return {
        "relationship": _validated_relationship(rel),
        "version": max(0, int(_num(raw.get("version"), 0))),
        "updated_ts": _num(raw.get("updated_ts"), 0.0),
        "path": str(path),
    }


def _acquire_lock(path):
    lock = Path(str(path) + ".lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    deadline = time.time() + LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, b"1")
            finally:
                os.close(fd)
            return lock
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            except Exception:
                pass
            if time.time() >= deadline:
                return None  # last-write-wins fallback, still versioned
            time.sleep(0.005)
        except OSError:
            # Transient Windows sharing/lock races: retry until the deadline.
            if time.time() >= deadline:
                return None
            time.sleep(0.005)


def _release_lock(lock):
    if lock is None:
        return
    try:
        lock.unlink()
    except Exception:
        pass


def relationship_update(store, deltas=None, now=None, attachment_style=None,
                        bids=None, days_known=None):
    """Versioned, locked update of shared relationship values; deltas are capped."""
    root = _store_root(store)
    if root is None:
        return {"ok": False, "reason": "no_store",
                "relationship": dict(DEFAULT_RELATIONSHIP), "version": 0}
    path = relationship_path(store)
    lock = _acquire_lock(path)
    try:
        current = relationship_read(store)
        value = dict(current["relationship"])
        data = deltas if isinstance(deltas, dict) else {}
        for key in RELATIONSHIP_FIELDS:
            if key not in data:
                continue
            cap = RELATIONSHIP_DELTA_LIMITS[key]
            delta = _clamp(_num(data.get(key), 0.0), -cap, cap)
            value[key] = round(_clamp(value[key] + delta, 0.0, 1.0), 4)
        if isinstance(attachment_style, str):
            style = attachment_style.strip().lower()
            if style in ATTACHMENT_STYLES:
                value["attachment_style"] = style
        if isinstance(bids, dict):
            for key in ("made", "answered"):
                if key in bids:
                    step = int(_num(bids.get(key), 0))
                    step = max(-3, min(3, step))
                    value["bids"][key] = max(0, value["bids"][key] + step)
        if days_known is not None:
            value["days_known"] = max(value["days_known"],
                                      max(0, int(_num(days_known, 0))))
        stamp = _num(now, time.time())
        doc = {"schema_version": 1, "version": current["version"] + 1,
               "updated_ts": round(stamp, 3),
               "relationship": _validated_relationship(value)}
        try:
            _atomic_write(path, doc)
        except Exception:
            return {"ok": False, "reason": "write_failed",
                    "relationship": current["relationship"],
                    "version": current["version"]}
        return {"ok": True, "relationship": doc["relationship"],
                "version": doc["version"], "path": str(path)}
    finally:
        _release_lock(lock)


def _load_session_state(store, session_id):
    path = state_path(store, session_id)
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _save_session_state(store, session_id, state):
    path = state_path(store, session_id)
    if path is None:
        return False
    try:
        _atomic_write(path, state)
        return True
    except Exception:
        return False


def _shared_relationship_enabled(payload):
    data = payload if isinstance(payload, dict) else {}
    switches = data.get("switches") if isinstance(data.get("switches"), dict) else {}
    raw = data.get("relationship_shared")
    if raw is None:
        raw = switches.get("relationship_shared")
    return _as_bool(raw, True)


def _relationship_store(payload):
    data = payload if isinstance(payload, dict) else {}
    raw = data.get("relationship_store") or data.get("store")
    return raw or None


def merge_shared_relationship_into_state(session_id, payload):
    """turn_start: shared relationship wins for the six fields of this session."""
    store = _relationship_store(payload)
    if store is None or not _shared_relationship_enabled(payload):
        return None
    sid = sanitize_session_id(session_id)
    shared = relationship_read(store)["relationship"]
    state = _load_session_state(store, sid)
    session_rel = state.get("relationship")
    merged = dict(session_rel) if isinstance(session_rel, dict) else {}
    merged.update(shared)
    state["relationship"] = merged
    state.setdefault("session_id", sid)
    _save_session_state(store, sid, state)
    base_path = session_dir(store, sid) / RELATIONSHIP_BASE_FILE
    try:
        _atomic_write(base_path, {"relationship": shared,
                                  "base_ts": round(time.time(), 3)})
    except Exception:
        pass
    return shared


def merge_state_relationship_into_shared(session_id, payload):
    """turn_end: bounded session changes are folded back into shared relationship."""
    store = _relationship_store(payload)
    if store is None or not _shared_relationship_enabled(payload):
        return None
    sid = sanitize_session_id(session_id)
    shared_doc = relationship_read(store)
    shared = shared_doc["relationship"]
    state = _load_session_state(store, sid)
    session_rel = state.get("relationship")
    session_rel = session_rel if isinstance(session_rel, dict) else {}
    base_path = session_dir(store, sid) / RELATIONSHIP_BASE_FILE
    base_doc = _load_json(base_path, {})
    base = base_doc.get("relationship") if isinstance(base_doc.get("relationship"), dict) else shared
    deltas = {}
    for key in RELATIONSHIP_FIELDS:
        try:
            session_value = float(session_rel.get(key, base.get(key, shared[key])))
            base_value = float(base.get(key, shared[key]))
        except Exception:
            continue
        deltas[key] = round(session_value - base_value, 4)
    # Conservative: conflict rising halves the trust drop allowed this turn.
    if deltas.get("conflict", 0.0) > 0.0 and deltas.get("trust", 0.0) < 0.0:
        deltas["trust"] = max(deltas["trust"], -0.01)
    result = relationship_update(
        store, deltas=deltas, now=time.time(),
        attachment_style=session_rel.get("attachment_style"),
        bids=session_rel.get("bids"),
        days_known=session_rel.get("days_known"))
    updated = result.get("relationship") or shared
    merged = dict(session_rel)
    merged.update(updated)
    state["relationship"] = merged
    _save_session_state(store, sid, state)
    try:
        if base_path.exists():
            base_path.unlink()
    except Exception:
        pass
    return updated


# ---------------------------------------------------------------- persona profile
PERSONA_PROFILE_FILE = "persona_profile.json"
PERSONA_APPLY_ORDER = ("traits", "affect", "drives", "autonomy")
PERSONA_FIELDS = (
    "O", "C", "E", "A", "N", "stress_reactivity", "recovery_rate",
    "social_baseline", "energy_baseline", "curiosity_baseline",
    "safety_baseline", "da_baseline", "oa_baseline", "ht_baseline",
    "autonomy_gain", "baseline_v", "baseline_a", "learning_rate",
)
PERSONA_RANGES = {
    "O": (0.0, 1.0), "C": (0.0, 1.0), "E": (0.0, 1.0),
    "A": (0.0, 1.0), "N": (0.0, 1.0),
    "stress_reactivity": (0.0, 1.0), "recovery_rate": (0.0, 1.0),
    "social_baseline": (0.0, 1.0), "energy_baseline": (0.0, 1.0),
    "curiosity_baseline": (0.0, 1.0), "safety_baseline": (0.0, 1.0),
    "da_baseline": (0.0, 1.0), "oa_baseline": (0.0, 1.0),
    "ht_baseline": (0.0, 1.0), "autonomy_gain": (0.0, 1.5),
    "baseline_v": (-1.0, 1.0), "baseline_a": (0.0, 1.0),
    "learning_rate": (0.0, 0.5),
}


def _persona_home():
    override = str(os.environ.get("BIOCORE_HOME") or "").strip()
    return Path(override) if override else Path.home()


def persona_profile_path(payload):
    """Explicit path -> ~/.dsh/biocore shared -> store -> store parent."""
    data = payload if isinstance(payload, dict) else {}
    explicit = data.get("persona_profile") or data.get("persona_profile_path")
    if isinstance(explicit, str) and explicit.strip():
        return Path(explicit.strip())  # explicit wins even if the file is missing
    candidates = []
    try:
        candidates.append(_persona_home() / ".dsh" / "biocore"
                          / PERSONA_PROFILE_FILE)
    except Exception:
        pass
    root = _store_root(_relationship_store(payload))
    if root is not None:
        candidates.append(root / PERSONA_PROFILE_FILE)
        candidates.append(root.parent / PERSONA_PROFILE_FILE)
    data_dir = data.get("data_dir")
    if data_dir:
        candidates.append(Path(str(data_dir)) / PERSONA_PROFILE_FILE)
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def load_persona_profile(payload):
    """Load + bound persona profile; missing/bad file -> None (silent skip)."""
    data = payload if isinstance(payload, dict) else {}
    inline = data.get("persona_profile")
    doc = None
    path = ""
    if isinstance(inline, dict):
        doc = inline
    else:
        candidate = persona_profile_path(data)
        if candidate is None:
            return None
        try:
            if not candidate.exists():
                return None
            doc = json.loads(candidate.read_text(encoding="utf-8"))
            path = str(candidate)
        except Exception:
            return None
    if not isinstance(doc, dict):
        return None
    raw = doc.get("profile") if isinstance(doc.get("profile"), dict) else doc
    fields = {}
    for key in PERSONA_FIELDS:
        if key not in raw:
            continue
        low, high = PERSONA_RANGES[key]
        fields[key] = round(_clamp(_num(raw.get(key), low), low, high), 4)
    if not fields:
        return None
    version = str(raw.get("version") or doc.get("persona_hash") or "")
    if not version:
        version = hashlib.sha256(
            json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
    persona_text = doc.get("persona") if isinstance(doc.get("persona"), str) else ""
    return {"fields": fields, "version": version[:64], "path": path,
            "persona_text": persona_text}


PERSONA_LINE_LIMIT = 160
THINKING_STYLE_TEXT = "思考就是我的内心想法。"
THINKING_STYLE_PREFIX = ""
THINKING_STYLE_LIMIT = 60
PERSONA_PREFIX = "人设："
_SENTENCE_END = ("。", "！", "？", "!", "?")


def _clip_persona_text(text, budget):
    text = _WS.sub(" ", str(text or "")).strip()
    if budget <= 0 or len(text) <= budget:
        return text  # 0 / negative = no truncation (full original persona)
    head = text[:budget]
    cut = 0
    for index in range(budget - 1, -1, -1):
        if head[index] in _SENTENCE_END:
            cut = index + 1
            break
    return head[:cut] if cut else head


def _numeric_persona_sentence(fields):
    fields = fields if isinstance(fields, dict) else {}
    parts = []
    energy = fields.get("E")
    agree = fields.get("A")
    neurotic = fields.get("N")
    order = fields.get("C")
    openness = fields.get("O")
    baseline_v = fields.get("baseline_v")
    if energy is not None:
        parts.append("偏外向" if energy >= 0.6 else ("偏内敛" if energy <= 0.4 else "内外平衡"))
    if agree is not None:
        parts.append("宜人" if agree >= 0.6 else ("有棱角" if agree <= 0.4 else "待人温和"))
    if neurotic is not None:
        parts.append("情绪敏感" if neurotic >= 0.6 else ("情绪稳定" if neurotic <= 0.4 else "情绪中等"))
    if baseline_v is not None:
        parts.append("情绪基线略高" if baseline_v >= 0.1 else
                     ("情绪基线偏低" if baseline_v <= -0.1 else "情绪基线平稳"))
    if order is not None and order >= 0.65:
        parts.append("做事严谨")
    if openness is not None and openness >= 0.65:
        parts.append("好奇")
    if not parts:
        return ""
    return "、".join(parts) + "；先接情绪再谈事"


def thinking_style_line(payload):
    """Universal expression rule (independent of any persona)."""
    data = payload if isinstance(payload, dict) else {}
    if not _as_bool(data.get("thinking_style"), True):
        return ""
    line = THINKING_STYLE_TEXT
    if len(line) > THINKING_STYLE_LIMIT:
        line = line[:THINKING_STYLE_LIMIT - 1] + "…"
    return line


def persona_prompt_line(payload):
    """Persona original text (<=160-char line) or a numeric fallback line."""
    data = payload if isinstance(payload, dict) else {}
    if not _as_bool(data.get("persona_prompt"), True):
        return ""
    profile = load_persona_profile(data)
    if not profile:
        return ""
    raw_budget = data.get("persona_prompt_max_chars", 0)
    try:
        budget = 0 if isinstance(raw_budget, bool) else int(raw_budget)
    except Exception:
        budget = 0
    budget = max(0, min(100000, budget))
    content = _clip_persona_text(profile.get("persona_text"), budget)
    if not content:
        content = _numeric_persona_sentence(profile.get("fields"))
    if not content:
        return ""
    return PERSONA_PREFIX + content


def apply_persona_to_state(state, profile):
    """Whitelisted persona -> session state (traits -> affect -> drives -> autonomy)."""
    if not isinstance(profile, dict) or not profile.get("fields"):
        return state if isinstance(state, dict) else {}
    state = dict(state) if isinstance(state, dict) else {}
    core = dict(state.get("core")) if isinstance(state.get("core"), dict) else {}
    fields = profile["fields"]
    applied = {}
    # 1) traits: validated but never written into memory/identity/safety policy.
    for key in ("O", "C", "E", "A", "N", "stress_reactivity"):
        if key in fields:
            applied[key] = fields[key]
    # 2) affect baseline
    if "baseline_v" in fields:
        core["v"] = round(fields["baseline_v"], 3)
        applied["baseline_v"] = fields["baseline_v"]
    if "baseline_a" in fields:
        core["a"] = round(fields["baseline_a"], 3)
        applied["baseline_a"] = fields["baseline_a"]
    # 3) drives: social/energy/curiosity/safety + recovery -> decay half-life
    for key, target in (("social_baseline", "social"),
                        ("energy_baseline", "energy"),
                        ("curiosity_baseline", "curiosity"),
                        ("safety_baseline", "safety")):
        if key in fields:
            core[target] = round(fields[key], 3)
            core[key] = round(fields[key], 3)
            applied[key] = fields[key]
    recovery = _clamp(_num(fields.get("recovery_rate"), 0.7), 0.0, 1.0)
    factor = round(1.25 - 0.50 * recovery, 3)
    core["valence_decay_minutes"] = round(120.0 * factor, 1)
    core["arousal_decay_minutes"] = round(30.0 * factor, 1)
    core["social_decay_minutes"] = round(180.0 * factor, 1)
    if "recovery_rate" in fields:
        applied["recovery_rate"] = fields["recovery_rate"]
    # 4) autonomy / learning
    for key in ("autonomy_gain", "learning_rate"):
        if key in fields:
            core[key] = round(fields[key], 4)
            applied[key] = fields[key]
    state["core"] = core
    state["persona_version"] = profile["version"]
    state["persona_profile"] = dict(applied, version=profile["version"])
    return state


def apply_persona_to_session(session_id, payload):
    """Apply persona to the session state file (or in-memory state if no store)."""
    profile = load_persona_profile(payload)
    store = _relationship_store(payload)
    session_id = sanitize_session_id(session_id)
    if profile is None and store is None:
        return None
    state = _load_session_state(store, session_id) if store is not None else {}
    if profile is not None:
        state = apply_persona_to_state(state, profile)
        if store is not None:
            _save_session_state(store, session_id, state)
    return state




# ---------------------------------------------------------------- core state
def _core_state_path(payload):
    """<dataDir>/core_state.json for any payload (store = <dataDir>/memory)."""
    if _core_state is None:
        return None
    data = payload if isinstance(payload, dict) else {}
    try:
        return _core_state.path_for(data.get("store"),
                                    data.get("data_dir") or data.get("dataDir"))
    except Exception:
        return None


def _intero_from_core(doc, prev, dt_hours, now):
    """Recompute intero from the global body, using its own intero as prev."""
    body = dict(doc) if isinstance(doc, dict) else {}
    merged = {"core": body}
    if isinstance(prev, dict) and prev:
        merged["core"] = dict(body, intero=prev)
        merged["intero"] = dict(prev)
    return compute_intero(merged, dt_hours=dt_hours or 0.0, now=now)


def _state_deltas(payload):
    """This turn's numeric deltas: direct dict, or diagnostics before/after."""
    data = payload if isinstance(payload, dict) else {}
    out = {}

    def add(key, value):
        if key not in _CORE_FIELDS:
            return
        try:
            out[key] = out.get(key, 0.0) + float(value)
        except Exception:
            pass

    for source_key in ("delta", "deltas", "state_delta"):
        raw = data.get(source_key)
        if isinstance(raw, dict):
            for key, value in raw.items():
                add(str(key), value)
        elif isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                add(str(item.get("field") or item.get("key") or ""),
                    _num(item.get("after"), 0.0) - _num(item.get("before"), 0.0))
    return {key: round(value, 4) for key, value in out.items()
            if abs(value) > 1e-9}


_CORE_FIELDS = ("v", "a", "stress", "energy", "social", "social_pain",
                "trust", "closeness", "conflict")


def _recall_count(store, session_id):
    """Recalled memory hits for this session (0 when traces are empty)."""
    try:
        total = 0
        for row in trace_read(store, session_id, TRACE_KEEP):
            memory = row.get("memory") if isinstance(row.get("memory"), dict) else {}
            ids = memory.get("recalled_ids")
            if isinstance(ids, list):
                total += len(ids)
        return total
    except Exception:
        return 0


def _proactive_sent_today(store):
    """Messages delivered by the proactive path today (0 when none)."""
    try:
        root = _store_root(store)
        if root is None:
            return 0
        data = _load_json(root / "proactive.json", {})
        sent = data.get("sent") if isinstance(data.get("sent"), list) else []
        now = time.time()
        start = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        return sum(1 for item in sent if _num(item, 0.0) / 1000.0 >= start)
    except Exception:
        return 0


def _series_payload(store, payload=None):
    """Newest <=24 points from the global history + growth_history.jsonl."""
    data = payload if isinstance(payload, dict) else {}
    limit = _int_param(data.get("limit"), 24, 1, 288)
    rows = []
    try:
        rows = _core_state.points(_core_state_path(data), limit) if _core_state else []
    except Exception:
        rows = []
    growth = []
    try:
        root = _store_root(store)
        path = (root / "growth_history.jsonl") if root is not None else None
        if path is not None and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict) and _num(item.get("ts"), 0.0) > 0:
                    growth.append({"ts": round(_num(item.get("ts")), 3),
                                   "trust": round(_num(item.get("trust"), 0.5), 4),
                                   "closeness": round(_num(item.get("closeness"),
                                                           0.5), 4)})
    except Exception:
        growth = []
    if growth:
        known = {round(_num(row.get("ts"), 0.0), 0) for row in rows}
        for item in growth[-limit:]:
            if round(_num(item.get("ts"), 0.0), 0) not in known:
                rows.append(item)
        rows.sort(key=lambda row: _num(row.get("ts"), 0.0))
        rows = rows[-limit:]
    series = {key: [row.get(key) for row in rows
                    if isinstance(row, dict) and row.get(key) is not None]
              for key in ("ts", "v", "tension", "warmth", "heaviness",
                          "clarity", "trust", "closeness")}
    return {"ok": True, "series": series, "points": rows,
            "source": "core_state" if _core_state else "unavailable"}


_RUNTIME_CONFIG = {}
CORE_CONFIG_FILE = "core_config.json"
CORE_DEFAULTS = {
    # contract: everything core-side is on; only external/irreversible off.
    "core_enabled": True,
    "mood_recall": True, "affect_encoding": True,
    "memory_feedback": True, "preference_drift": True,
    "governance": True, "index": True, "consolidate": True,
    "evict": True, "evict_delete": False,
    "relationship_governance": True,
    "self_layer": True, "motivation": True,
    "autonomy": True, "standby": False, "retrieval_plus": True,
    "safety_enabled": False, "boundary_enabled": False,
    "crisis_safety_enabled": False,
    "proactive_send": True,
    "recall_mode": "query",
}


def _load_runtime_config(store):
    data_dir = _store_data_dir(store)
    doc = {}
    if data_dir is not None:
        doc = _load_json(data_dir / CORE_CONFIG_FILE, {})
    if not isinstance(doc, dict) or not doc:
        doc = _RUNTIME_CONFIG
    return doc if isinstance(doc, dict) else {}


def configure(payload):
    """task-223: whole config arrives once; the core owns every decision."""
    data = payload if isinstance(payload, dict) else {}
    store = data.get("store")
    data_dir = data.get("data_dir")
    if not data_dir:
        data_dir = _store_data_dir(store) if store else None
    doc = {key: value for key, value in data.items()
           if key not in ("store", "data_dir")}
    doc["updated_at"] = round(time.time(), 3)
    if data_dir is not None:
        try:
            _atomic_write(Path(data_dir) / CORE_CONFIG_FILE, doc)
        except Exception:
            pass
    _RUNTIME_CONFIG.clear()
    _RUNTIME_CONFIG.update(doc)
    return {"ok": True, "configured": True,
            "switches": doc.get("switches") if isinstance(
                doc.get("switches"), dict) else {}}


def _effective_switches(store, overrides=None):
    merged = dict(CORE_DEFAULTS)
    data_dir = _store_data_dir(store)
    if data_dir is not None:
        flags = _load_json(data_dir / "flags.json", {})
        for key, value in flags.items():
            if isinstance(value, bool):
                merged[str(key)] = value
    cfg = _load_runtime_config(store)
    switches = cfg.get("switches") if isinstance(cfg.get("switches"), dict) else {}
    for key, value in switches.items():
        if isinstance(value, bool):
            merged[str(key)] = value
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if isinstance(value, bool):
                merged[str(key)] = value
    return merged


def _rerank_config_from(raw):
    """Accept either the adapter's rerank payload or config.json's rerank block."""
    if not isinstance(raw, dict):
        return None
    if "rerank_enabled" in raw:
        return raw
    return {"rerank_enabled": raw.get("enabled") is True,
            "rerank_base_url": str(raw.get("base_url") or ""),
            "rerank_model": str(raw.get("model") or ""),
            "rerank_api_key": str(raw.get("api_key") or ""),
            "rerank_top_n": int(raw.get("top_n") or 20),
            "rerank_timeout": 8}


def _recall_mode(store, requested=None):
    mode = str(requested or "").strip().lower()
    if mode in INJECT_MODES:
        return mode
    cfg = _load_runtime_config(store)
    mode = str(cfg.get("recall_mode") or "").strip().lower()
    return mode if mode in INJECT_MODES else "query"


MOTIVATIONS = {
    "hold": "想先听着，不急着推进",
    "approach": "想靠近一点，再问一句",
    "withdraw": "想退半步，留点空间",
    "probe": "想探一探，但还不确定",
    "repair": "想把话圆回来",
    "rest": "有点累，想安静待着",
    "share": "想说说自己这边的事",
}


def _ui_state_body(store, session_id, data):
    """task-223: the /state payload is assembled by the core, JS forwards it."""
    entries = _visible(load_memory(store, session_id).get("entries"))
    stored_state = _load_session_state(store, session_id)
    core_path = _core_state_path(data)
    core_state_ts = 0.0
    if _core_state is not None and _core_state.exists(core_path):
        doc = _core_state.load(core_path)
        core = _core_state.core_of(doc)
        intero = doc.get("intero")
        if not isinstance(intero, dict):
            intero = compute_intero({"core": core})
        core["intero"] = intero
        core_state_ts = doc.get("ts", 0.0)
        history = list(doc.get("history") or [])[-120:]
    else:
        merged_state = apply_intero_to_state(stored_state, data.get("state"))
        intero = merged_state.get("intero") or {}
        core = dict(merged_state.get("core") or {})
        core["intero"] = intero
        history = list(stored_state.get("history") or [])[-120:]
    relationship = (relationship_read(store)["relationship"]
                    if _store_root(store) is not None else {})
    recent = []
    for entry in sorted(entries, key=lambda item: _num(item.get("ts"), 0.0),
                        reverse=True)[:10]:
        text = clean_text(entry.get("text"), 500)
        if not text:
            continue
        recent.append({"ts": round(_num(entry.get("ts"), 0.0) * 1000, 0),
                       "text": text[:80],
                       "source": str(entry.get("memory_source") or "direct"),
                       "kind": str(entry.get("kind") or "")})
    rel_metrics = {"closeness": round(_num(relationship.get("closeness"), 0.5), 2),
                   "trust": round(_num(relationship.get("trust"), 0.5), 2),
                   "conflict": round(_num(relationship.get("conflict"), 0.0), 2),
                   "bids": round(_num(relationship.get("bids"), 0.0), 2)}
    body = {"self_esteem": round(_num(core.get("self_esteem"), 0.0), 2),
            "social_pain": round(_num(core.get("social_pain"), 0.0), 2),
            "energy": round(_num(core.get("energy"), 0.5), 2),
            "social": round(_num(core.get("social"), 0.5), 2)}
    trace_rows = trace_read(store, session_id, 1)
    last_trace = trace_rows[-1] if trace_rows else {}
    last_turn = last_trace.get("intent") if isinstance(last_trace, dict) else {}
    intent = str((last_turn or {}).get("kind") or "")[:24]
    governance_summary = None
    if _governance is not None:
        try:
            governance_summary = _governance.summary(store)
        except Exception:
            governance_summary = None
    relationship_summary = None
    if _relationship_state is not None:
        try:
            relationship_summary = _relationship_state.summary(store, relationship)
        except Exception:
            relationship_summary = None
    identity_summary = None
    if _self_layer is not None:
        try:
            identity_summary = _self_layer.summary(store)
        except Exception:
            identity_summary = None
    autonomy_view = None
    if _autonomy is not None:
        try:
            switches = _effective_switches(store)
            metrics = _autonomy.metrics(store)
            autonomy_view = {
                "enabled": bool(switches.get("autonomy", True)),
                "actions_today": metrics["actions_today"],
                "sends_today": metrics["sends_today"],
                "actions_hour": metrics["actions_hour"],
                "sends_hour": metrics["sends_hour"],
                "limits": _autonomy.limits(store)}
        except Exception:
            autonomy_view = None
    standby_view = None
    try:
        from . import standby as _standby_view
    except Exception:
        _standby_view = None
    if _standby_view is not None:
        try:
            standby_view = _standby_view.summary(
                store, enabled=bool(_effective_switches(store).get("standby")))
        except Exception:
            standby_view = None
    signals_summary = None
    if _signals is not None:
        try:
            signals_summary = _signals.summary(store)
        except Exception:
            signals_summary = None
    extensions_summary = None
    if _extensions is not None:
        try:
            extensions_summary = _extensions.extension_summary(store)
        except Exception:
            extensions_summary = None
    identity = {"v": round(_num(core.get("v"), 0.0), 2),
                "intero": intero,
                "governance": governance_summary,
                "relationship_summary": relationship_summary,
                "identity": identity_summary,
                "signals": signals_summary,
                "extensions": extensions_summary,
                "standby": standby_view,
                "recall_debug": (_retrieval_plus.summary(store)
                                 if _retrieval_plus is not None else None),
                "tiers": (_lifecycle.tier_summary(store)
                          if _lifecycle is not None else None),
                "reconsolidation": (_lifecycle.reconsolidation_summary(store)
                                    if _lifecycle is not None else None),
                "repair": (_lifecycle.repair_state(store)
                           if _lifecycle is not None else None),
                "available": True,
                "session_id": sanitize_session_id(session_id),
                "memory_count": len(entries),
                "memory": {"count": len(entries)},
                "memory_recent": recent,
                "relationship_metrics": rel_metrics,
                "body_metrics": body,
                "narrative": {"intent": intent, "length_factor": 1.0,
                              "initiative": 0.5, "metacog_band": "",
                              "allow_correction": False},
                "motivation": MOTIVATIONS.get(intent, "安静待着，等你开口"),
                "tone": {"label": ""},
                "relation": {"stage": ""},
                "autonomy": {"state": "静", **((autonomy_view or {}))},
                "history": history,
                "core_state_ts": core_state_ts,
                "recall_count": _recall_count(store, session_id),
                "governance_numbers": (governance_summary or {}),
                "proactive_sent_today": _proactive_sent_today(store),
                "core": core,
                "source": "core"}
    return identity


def _embed_stats_snapshot():
    """Embedding counters: persisted file (spawn-per-call) wins over process."""
    data = {}
    try:
        from biocore_memory.recall.embedding import embed_stats as _embed_stats
        data = dict(_embed_stats())
    except Exception:
        data = {}
    path = str(os.environ.get("BIOCORE_EMBED_STATS_FILE") or "").strip()
    if path and os.path.isfile(path):
        persisted = _load_json(Path(path), {})
        if isinstance(persisted, dict) and persisted:
            data = {key: int(persisted.get(key, data.get(key, 0)) or 0)
                    for key in ("hit", "disk_hit", "miss", "remote",
                                "local", "fail")}
    return data


# ---------------------------------------------------------------- turn trace
TRACE_DIR = "traces"
TRACE_KEEP = 50
TRACE_DEFAULT_LIMIT = 20


def trace_path(store, session_id):
    root = _store_root(store)
    if root is None:
        return None
    return root / TRACE_DIR / (sanitize_session_id(session_id) + ".jsonl")


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _trace_state_delta(raw):
    rows = []

    def add(key, before, after, source=""):
        if not key:
            return
        if isinstance(before, bool) or isinstance(after, bool):
            return
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            return
        if abs(float(after) - float(before)) < 1e-9:
            return  # only values that actually changed
        origin = str(source or "").strip().lower()
        if origin in ("", "user", "bio", "bio_tick", "biotick"):
            origin = "bio_tick" if origin in ("bio", "bio_tick", "biotick") else "user"
        else:
            origin = origin[:20]
        rows.append({"field": str(key)[:40],
                     "before": round(float(before), 4),
                     "after": round(float(after), 4),
                     "source": origin})

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            add(item.get("field") or item.get("key") or item.get("name"),
                item.get("before"), item.get("after"), item.get("source"))
    elif isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict) and "before" in value and "after" in value:
                add(key, value.get("before"), value.get("after"),
                    value.get("source"))
    return rows[:20]


def _trace_intent(raw):
    data = raw if isinstance(raw, dict) else {}
    return {
        "kind": str(data.get("kind") or "")[:20],
        "weight": round(_clamp(_num(data.get("weight"), 0.0), 0.0, 1.0), 4),
        "reason": str(data.get("reason") or "")[:120],
    }


def _trace_constraints(raw):
    data = raw if isinstance(raw, dict) else {}
    out = {}
    for key in ("length_scale", "initiative", "tone",
                "allow_correction", "note", "sources"):
        if key not in data:
            continue
        value = data.get(key)
        if key == "sources":
            out[key] = [str(item)[:40] for item in value][:10] if isinstance(value, list) else []
        elif key == "tone":
            out[key] = str(value or "")[:60]
        elif key == "note":
            out[key] = str(value or "")[:120]
        elif key == "allow_correction":
            out[key] = _as_bool(value, False)
        elif key == "length_scale":
            out[key] = round(_clamp(_num(value, 1.0), 0.5, 1.5), 4)
        else:
            out[key] = round(_clamp(_num(value, 0.5), 0.0, 1.0), 4)
    return out


def _trace_length(raw, constraints):
    data = raw if isinstance(raw, dict) else {}
    scale = data.get("scale")
    if scale is None:
        scale = constraints.get("length_scale", 1.0)
    source = str(data.get("source") or ("constraints" if constraints else "default"))[:40]
    return {"scale": round(_clamp(_num(scale, 1.0), 0.5, 1.5), 4),
            "source": source}


def _trace_memory(raw, captured):
    data = raw if isinstance(raw, dict) else {}
    ids = data.get("recalled_ids")
    ids = [str(item)[:40] for item in ids][:20] if isinstance(ids, list) else []
    return {"recalled_ids": ids,
            "captured_id": str((captured or {}).get("entry_id") or "")[:40]}


def _trace_modules(raw):
    data = raw if isinstance(raw, dict) else {}
    ran = data.get("ran") if isinstance(data.get("ran"), list) else []
    skipped = data.get("skipped") if isinstance(data.get("skipped"), list) else []
    return {"ran": [str(item)[:30] for item in ran][:20],
            "skipped": [str(item)[:30] for item in skipped][:20]}


def build_trace(session_id, payload, gate, captured):
    """Diagnostic row; purely observational (never drives gate/capture)."""
    data = payload if isinstance(payload, dict) else {}
    constraints = _trace_constraints(data.get("constraints"))
    return {
        "ts": round(time.time(), 3),
        "session_id": sanitize_session_id(session_id),
        "state_delta": _trace_state_delta(data.get("state_delta")),
        "intent": _trace_intent(data.get("intent")),
        "constraints": constraints,
        "length": _trace_length(data.get("length"), constraints),
        "gate": {"action": str((gate or {}).get("action") or "pass")[:20],
                 "rule": str((gate or {}).get("rule") or "")[:40],
                 "label": str((gate or {}).get("label") or "")[:40]},
        "memory": _trace_memory(data.get("memory"), captured),
        "modules": _trace_modules(data.get("modules_trace")),
    }


def _read_trace_rows(path):
    rows = []
    try:
        if path is None or not Path(path).exists():
            return rows
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue  # bad data is skipped, never breaks diagnostics
            if isinstance(row, dict):
                rows.append(row)
    except Exception:
        return []
    return rows


def trace_append(store, session_id, row, keep=TRACE_KEEP):
    """Append one row, keeping the newest keep rows; returns trace file path."""
    path = trace_path(store, session_id)
    if path is None or not isinstance(row, dict):
        return ""
    limit = _int_param(keep, TRACE_KEEP, 1, TRACE_KEEP)
    lock = _acquire_lock(path)
    try:
        rows = _read_trace_rows(path)
        rows.append(row)
        rows = rows[-limit:]
        try:
            _atomic_write_text(path, "\n".join(
                json.dumps(item, ensure_ascii=False,
                           separators=(",", ":")) for item in rows) + "\n")
        except Exception:
            return ""
        return str(path)
    finally:
        _release_lock(lock)


def trace_read(store, session_id, limit=TRACE_DEFAULT_LIMIT):
    """Read-only: newest limit valid rows (chronological order, bad lines skipped)."""
    path = trace_path(store, session_id)
    count = _int_param(limit, TRACE_DEFAULT_LIMIT, 1, TRACE_KEEP)
    return _read_trace_rows(path)[-count:]


# ---------------------------------------------------------------- CLI dispatch
def dispatch(action, payload=None):
    """One CLI action -> JSON-safe result (never raises)."""
    data = payload if isinstance(payload, dict) else {}
    action = str(action or data.get("action") or "").strip().lower()
    store = data.get("store") or _default_store()

    def bad(detail):
        return {"ok": False, "reason": "bad_request", "detail": str(detail)[:300]}

    try:
        if action == "boot":
            session_id = str(data.get("session_id") or "").strip()
            if not session_id:
                return bad("missing session_id")
            block = prompt_block(dict(
                data, phase="boot", session_id=session_id,
                state=data.get("state") or {},
                switches=data.get("switches"),
                max_chars=data.get("max_chars")))
            session_state = apply_persona_to_session(session_id, data)
            session_state = session_state if isinstance(session_state, dict) else {}
            session_state = apply_intero_to_state(session_state, data.get("state"))
            return {"ok": True, "prompt_block": block["text"],
                    "state": session_state}
        if action == "turn_start":
            session_id = str(data.get("session_id") or "").strip()
            if not session_id:
                return bad("missing session_id")
            if "user_text" not in data:
                return bad("missing user_text")
            user_text = clean_text(data.get("user_text"))
            switches = _effective_switches(store, data.get("switches"))
            data["switches"] = switches
            if not switches.get("core_enabled", True):
                return {"ok": True, "prompt_block": "", "state": {},
                        "disabled": True, "reason": "core_disabled"}
            merge_shared_relationship_into_state(session_id, data)
            session_state = apply_persona_to_session(session_id, data)
            session_state = session_state if isinstance(session_state, dict) else {}
            state_store = _relationship_store(data)
            core_path = _core_state_path(data)
            if _core_state is not None and core_path is not None:
                payload_state = (data.get("state")
                                 if isinstance(data.get("state"), dict) else {})
                payload_core = (payload_state.get("core")
                                if isinstance(payload_state.get("core"), dict)
                                else {})
                legacy_core = dict(payload_core)
                session_core = session_state.get("core")
                if isinstance(session_core, dict):
                    legacy_core.update(session_core)
                if _core_state.exists(core_path):
                    _core_state.migrate_session(core_path, legacy_core,
                                                session_id, now=time.time())
                else:
                    passed = {key: legacy_core[key] for key in _CORE_FIELDS
                              if key in legacy_core}
                    _core_state.apply(core_path, absolute=passed,
                                      now=time.time(), session_id=session_id,
                                      intero_fn=_intero_from_core)
                doc = _core_state.load(core_path)
                if doc.get("intero") is None:
                    _core_state.apply(core_path, now=time.time(),
                                      session_id=session_id,
                                      intero_fn=_intero_from_core)
                    doc = _core_state.load(core_path)
                core = dict(legacy_core)
                core.update(_core_state.core_of(doc))
                if isinstance(doc.get("intero"), dict):
                    core["intero"] = dict(doc["intero"])
                    session_state["intero"] = dict(doc["intero"])
                session_state["core"] = core
                session_state["core_delta"] = {}
                session_state["core_state_ts"] = doc.get("ts", 0.0)
            else:
                session_state = apply_intero_to_state(session_state,
                                                      data.get("state"))
            session_state["last_human_ts"] = time.time()
            if state_store is not None:
                _save_session_state(state_store, session_id, session_state)
            _signals_eval(store, session_id, user_text=user_text,
                          state=session_state, now=time.time())
            capture_enabled = _as_bool(data.get("capture"), True)
            captured_user = {"captured": False, "entry_id": "", "reason": "disabled"}
            capture_store = data.get("store")
            if capture_enabled and capture_store:
                user_capture = capture(session_id, "user", user_text, capture_store, {
                    "enabled": _as_bool(data.get("memoryEnabled", True), True),
                    "hourly_cap": data.get("hourly_cap", HOURLY_CAP),
                    "min_chars": data.get("min_chars", MIN_CHARS),
                })
                decision = user_capture.get("decision") or {}
                entry = user_capture.get("entry") or {}
                captured_user = {
                    "captured": bool(decision.get("capture")),
                    "entry_id": str(entry.get("id") or ""),
                    "reason": str(decision.get("reason") or ""),
                }
            elif capture_enabled:
                captured_user["reason"] = "no_store"
            block = prompt_block(dict(
                data, phase="turn", session_id=session_id,
                user_text=user_text,
                state=session_state,
                switches=data.get("switches"),
                max_chars=data.get("max_chars"),
                length_target=data.get("length_target"),
                length_max=data.get("length_max"),
                allow_correction=data.get("allow_correction")))
            return {"ok": True, "prompt_block": block["text"],
                    "constraints": self_layer_constraints(
                        dict(data, user_text=user_text)),
                    "state": session_state,
                    "captured_user": captured_user}
        if action == "turn_end":
            session_id = str(data.get("session_id") or "").strip()
            if not session_id:
                return bad("missing session_id")
            if "assistant_text" not in data:
                return bad("missing assistant_text")
            switches = _effective_switches(store, data.get("switches"))
            data["switches"] = switches
            if not switches.get("core_enabled", True):
                return {"ok": True, "gate": {"blocked": False, "action": "pass",
                                             "text": str(data.get("assistant_text") or ""),
                                             "rule": "", "label": ""},
                        "captured": None, "clean_text": str(data.get("assistant_text") or ""),
                        "trace": None, "trace_file": "", "disabled": True,
                        "reason": "core_disabled"}
            raw_assistant = clean_text(data.get("assistant_text"))
            do_sanitize = _as_bool(data.get("sanitize_reply"), True)
            cleaned = raw_assistant
            sanitized = False
            removed_tags = 0
            if do_sanitize and _sanitize is not None:
                stats = getattr(_sanitize, "STATS", {}) or {}
                before = (int(stats.get("tag_stripped", 0) or 0),
                          int(stats.get("json_stripped", 0) or 0),
                          int(stats.get("fenced_stripped", 0) or 0))
                try:
                    cleaned = _sanitize.clean(raw_assistant)
                except Exception:
                    cleaned = raw_assistant
                after = (int(stats.get("tag_stripped", 0) or 0),
                         int(stats.get("json_stripped", 0) or 0),
                         int(stats.get("fenced_stripped", 0) or 0))
                removed_tags = max(0, sum(after) - sum(before))
                sanitized = cleaned != raw_assistant or removed_tags > 0
            assistant_text = cleaned
            switches = normalize_switches(data.get("switches"))
            gate = gate_reply(assistant_text, {
                "switches": switches, "user_text": data.get("user_text", "")})
            if do_sanitize and _sanitize is not None:
                try:
                    gate = dict(gate)
                    gate["text"] = _sanitize.clean(gate.get("text") or "")
                except Exception:
                    pass
            capture_enabled = _as_bool(data.get("capture"), True)
            capture_store = data.get("store")
            if capture_enabled and capture_store:
                captured = capture(session_id, "assistant", assistant_text,
                                   capture_store, {
                    "enabled": _as_bool(data.get("memoryEnabled",
                                                 switches.get("memoryEnabled", True)),
                                        True),
                    "hourly_cap": data.get("hourly_cap", HOURLY_CAP),
                    "min_chars": data.get("min_chars", MIN_CHARS),
                })
            else:
                captured = {"ok": True, "decision": {
                    "capture": False,
                    "reason": "disabled" if not capture_enabled else "no_store",
                }, "entry": None}
            merge_state_relationship_into_shared(session_id, data)
            core_snapshot = None
            core_path = _core_state_path(data)
            if _core_state is not None and core_path is not None:
                deltas = _state_deltas(data)
                session_doc = (_load_session_state(store, session_id)
                               if store is not None else {})
                legacy_core = session_doc.get("core")
                legacy_core = legacy_core if isinstance(legacy_core, dict) else {}
                if not _core_state.exists(core_path):
                    passed = {key: legacy_core[key] for key in _CORE_FIELDS
                              if key in legacy_core}
                    _core_state.apply(core_path, absolute=passed,
                                      now=time.time(), session_id=session_id,
                                      intero_fn=_intero_from_core)
                shared_rel = (relationship_read(store)["relationship"]
                              if store is not None else {})
                fixed = {key: shared_rel[key]
                         for key in ("trust", "closeness", "conflict")
                         if isinstance(shared_rel.get(key), (int, float))}
                doc = _core_state.apply(core_path, deltas=deltas,
                                        absolute=fixed, now=time.time(),
                                        session_id=session_id,
                                        intero_fn=_intero_from_core)
                if doc:
                    core = dict(legacy_core)
                    core.update(_core_state.core_of(doc))
                    if isinstance(doc.get("intero"), dict):
                        core["intero"] = dict(doc["intero"])
                        session_doc["intero"] = dict(doc["intero"])
                    session_doc["core"] = core
                    session_doc["core_delta"] = dict(deltas)
                    session_doc["core_state_ts"] = doc.get("ts", 0.0)
                    if store is not None:
                        _save_session_state(store, session_id, session_doc)
                    core_snapshot = _core_state.snapshot(core_path)
            governance_info = _governance_tick(store, now=time.time())
            relationship_info = _relationship_tick(
                store, data.get("user_text"), now=time.time())
            signals_end = _signals_eval(
                store, session_id,
                user_text=str(data.get("user_text") or ""),
                assistant_text=assistant_text,
                state=(_load_session_state(store, session_id)
                       if store is not None else {}),
                now=time.time())
            if _extensions is not None:
                try:
                    all_entries = _visible(
                        load_memory(store, session_id).get("entries"))
                    picked_ids = [str(row.get("id") or "")
                                  for row in all_entries[:5]]
                    commitment = ((signals_end or {}).get("relationship", {})
                                  .get("commitment_extractor") or {})
                    if commitment.get("has_commitment"):
                        _extensions.add_commitment(
                            store, str(commitment.get("what") or ""),
                            float(commitment.get("due_ts") or 0.0),
                            now=time.time())
                    expression = _extensions.expression_block(
                        store, _load_session_state(store, session_id),
                        (relationship_read(store)["relationship"]
                         if store is not None else {}))
                    snapshot = {
                        "graph": {"entities": 0, "edges": 0},
                        "layered": _extensions.layered_view(all_entries,
                                                            picked_ids),
                        "prospective": _extensions.prospective_view(
                            store, time.time()),
                        "reconstruction": _extensions.reconstruction_view(
                            all_entries),
                        "turn_memory": _extensions.turn_memory_view(
                            [{"entry_id": row.get("id"),
                              "text": row.get("text"), "source": "recall"}
                             for row in all_entries[:5]],
                            reply=assistant_text, now=time.time()),
                        "expression": expression,
                    }
                    hits, graph_stats = _extensions.graph_hits(
                        all_entries, str(data.get("user_text") or ""))
                    snapshot["graph"] = graph_stats
                    _extensions.record_extension_snapshot(store, snapshot)
                except Exception:
                    pass
            if _lifecycle is not None:
                try:
                    _lifecycle.reconsolidate(store, now=time.time())
                    if gate.get("blocked"):
                        _lifecycle.arm_repair(store, gate.get("rule") or "",
                                              now=time.time())
                except Exception:
                    pass
            identity_info = None
            if relationship_info:
                identity_info = _identity_tick(
                    store, narrative={"kind": relationship_info.get("kind"),
                                      "text": str(data.get("user_text") or "")[:60]},
                    now=time.time())
            decision = captured.get("decision") or {}
            entry = captured.get("entry") or {}
            trace = build_trace(session_id, data, gate, {"entry_id": entry.get("id")})
            trace_file = trace_append(store, session_id, trace)
            return {
                "ok": True,
                "gate": {"blocked": gate["blocked"], "action": gate["action"],
                         "text": gate["text"], "rule": gate["rule"],
                         "label": gate["label"]},
                "captured": {"captured": bool(decision.get("capture")),
                             "entry_id": str(entry.get("id") or ""),
                             "reason": str(decision.get("reason") or "")},
                "clean_text": cleaned,
                "sanitized": bool(sanitized),
                "removed_tags": int(removed_tags),
                "trace": trace,
                "trace_file": trace_file,
                "core_state": core_snapshot,
                "governance": governance_info,
                "relationship": relationship_info,
                "identity": identity_info,
            }
        if action == "usage_event":
            if _cache_stats is None:
                return {"ok": False, "reason": "cache_stats_unavailable"}
            payload = data.get("event") if isinstance(data.get("event"), dict) else data
            row = _cache_stats.record_event(store, payload,
                                            now=data.get("now"))
            return {"ok": row is not None, "event": row,
                    "cache": _cache_stats.summary(store, now=data.get("now"))}
        if action == "stats":
            switches = _effective_switches(store, data.get("switches"))
            if not switches.get("core_enabled", True):
                return {"ok": True, "disabled": True, "reason": "core_disabled",
                        "embed": {key: 0 for key in ("hit", "disk_hit", "miss",
                                                     "remote", "local", "fail")},
                        "switches": {}, "store": str(store)}
            embed = _embed_stats_snapshot()
            cache_info = None
            if _cache_stats is not None:
                try:
                    cache_info = _cache_stats.summary(store)
                except Exception:
                    cache_info = None
            names = ("mood_recall", "affect_encoding", "memory_feedback",
                     "preference_drift")
            out = {name: {"enabled": bool(switches.get(name)), "source": "core"}
                   for name in names}
            out["core_enabled"] = {"enabled": True, "source": "core"}
            governance_info = None
            if _governance is not None:
                try:
                    governance_info = _governance.summary(store)
                except Exception:
                    governance_info = None
            relationship_info = None
            if _relationship_state is not None:
                try:
                    relationship_info = _relationship_state.summary(
                        store, relationship_read(store)["relationship"])
                except Exception:
                    relationship_info = None
            identity_info = None
            if _self_layer is not None:
                try:
                    identity_info = _self_layer.summary(store)
                except Exception:
                    identity_info = None
            autonomy_info = None
            if _autonomy is not None:
                try:
                    metrics = _autonomy.metrics(store)
                    autonomy_info = {"enabled": bool(switches.get("autonomy", True)),
                                     "actions_today": metrics["actions_today"],
                                     "sends_today": metrics["sends_today"],
                                     "limits": _autonomy.limits(store)}
                except Exception:
                    autonomy_info = None
            signals_info = None
            if _signals is not None:
                try:
                    signals_info = _signals.summary(store)
                except Exception:
                    signals_info = None
            return {"ok": True, "embed": embed, "switches": out,
                    "governance": governance_info,
                    "relationship": relationship_info,
                    "identity": identity_info,
                    "autonomy": autonomy_info,
                    "signals": signals_info,
                    "cache": cache_info,
                    "store": str(store)}
        if action == "standby_tick":
            try:
                from . import standby as _standby
            except Exception:
                from biocore_runtime import standby as _standby
            payload = data if isinstance(data, dict) else {}
            cfg = payload.get("standby")
            if not isinstance(cfg, dict):
                cfg = {}
            runtime = _load_runtime_config(store) if store is not None else {}
            section = runtime.get("standby")
            merged = dict(section) if isinstance(section, dict) else {}
            merged.update(cfg)
            merged["standby_store"] = str(store or "")
            merged["standby_session_id"] = str(
                payload.get("session_id") or "standby")
            return _standby.tick(now=payload.get("now"), config=merged)
        if action == "autonomy_tick":
            return _autonomy_tick(store, data) or {
                "ok": False, "reason": "autonomy_unavailable"}
        if action == "state":
            session_id = str(data.get("session_id") or "").strip()
            if not session_id:
                return bad("missing session_id")
            switches = _effective_switches(store, data.get("switches"))
            if not switches.get("core_enabled", True):
                return {"ok": True, "state": {"available": False,
                                              "reason": "core_disabled",
                                              "session_id": sanitize_session_id(session_id)}}
            body = _ui_state_body(store, session_id, data)
            stored_state = _load_session_state(store, session_id)
            if stored_state.get("persona_version"):
                body["persona_version"] = str(
                    stored_state.get("persona_version") or "")
            if _store_root(store) is not None:
                body["relationship"] = relationship_read(store)["relationship"]
            body["switches"] = {key: bool(switches.get(key)) for key in
                                ("core_enabled", "mood_recall",
                                 "affect_encoding", "memory_feedback",
                                 "preference_drift")}
            return {"ok": True, "state": body,
                    "ui_payload": {"intero": body.get("intero") or {}}}
        if action == "series":
            switches = _effective_switches(store, data.get("switches"))
            if not switches.get("core_enabled", True):
                return {"ok": True, "series": {key: [] for key in
                                               ("ts", "v", "tension", "warmth",
                                                "heaviness", "clarity", "trust",
                                                "closeness")},
                        "points": [], "source": "disabled", "disabled": True}
            return _series_payload(store, data)
        if action == "switches_get":
            current = normalize_switches(data.get("switches"))
            key = str(data.get("key") or "").strip()
            if key:
                if key not in SWITCH_KEYS:
                    return bad("unknown switch key")
                return {"ok": True, "switches": {key: current[key]}}
            return {"ok": True, "switches": current}
        if action == "switches_set":
            key = str(data.get("key") or "").strip()
            if key not in SWITCH_KEYS:
                return bad("unknown switch key")
            if not isinstance(data.get("value"), bool):
                return bad("value must be boolean")
            if data.get("confirm") != key:
                return bad("confirm must equal key")
            current = normalize_switches(data.get("switches"))
            current[key] = bool(data.get("value"))
            return {"ok": True, "switches": current}
        if action == "recall":
            query = str(data.get("query") or "").strip()
            documents = data.get("documents")
            if not isinstance(documents, list):
                documents = data.get("docs") if isinstance(data.get("docs"), list) else []
            fallback = [{"index": index,
                         "document": str((item.get("text") or item.get("document"))
                                         if isinstance(item, dict) else (item or "")).strip()}
                        for index, item in enumerate(documents)]
            if _rerank_mod is None:
                return {"ok": True, "reranked": False, "results": fallback,
                        "reason": "unavailable"}
            config = data.get("config") if isinstance(data.get("config"), dict) else data
            result = _rerank_mod.rerank_documents(query, documents, config)
            if not result:
                return {"ok": True, "reranked": False, "results": fallback}
            return {"ok": True, "reranked": True, "results": result}
        if action == "trace":
            session_id = str(data.get("session_id") or "").strip()
            if not session_id:
                return bad("missing session_id")
            rows = trace_read(store, session_id, data.get("limit"))
            return {"ok": True, "rows": rows}
        if action == "inject":
            return prompt_block(data)
        if action == "gate":
            reply = data.get("reply", data.get("reply_text", ""))
            return gate_reply(reply, data)
        if action == "capture":
            session_id = data.get("session_id") or data.get("session") or "default"
            switches = data.get("switches") if isinstance(data.get("switches"), dict) else {}
            enabled = _as_bool(switches.get("memoryEnabled", True), True)
            result = capture(session_id, data.get("role", "user"),
                             data.get("text", ""), store,
                             {"enabled": enabled,
                              "hourly_cap": data.get("hourly_cap", HOURLY_CAP),
                              "min_chars": data.get("min_chars", MIN_CHARS),
                              "now": data.get("now")})
            decision = result.get("decision") or {}
            entry = result.get("entry") or {}
            return {"ok": True, "captured": bool(decision.get("capture")),
                    "reason": str(decision.get("reason") or ""),
                    "entry_id": entry.get("id") or ""}
        if action == "tool":
            session_id = data.get("session_id") or data.get("session") or "default"
            result = tool_call(data.get("name", ""), data.get("args") or {},
                               store, session_id=session_id)
            if not isinstance(result, dict):
                result = {"reason": "tool_failed"}
            inner_ok = bool(result.get("ok"))
            flat = {key: value for key, value in result.items() if key != "ok"}
            return {"ok": inner_ok, "result": flat}
        if action == "proactive_tick":
            return proactive_tick(
                data.get("session_id") or data.get("session") or "",
                data.get("last_human_ts"), data.get("switches") or {},
                store, now=data.get("now"), state_file=data.get("state_file"))
        if action in ("memory_inject", "memory"):
            session_id = data.get("session_id") or data.get("session") or "default"
            switches = _effective_switches(store, data.get("switches"))
            if not switches.get("core_enabled", True):
                return {"ok": True, "text": "", "chars": 0, "ids": [],
                        "disabled": True, "reason": "core_disabled"}
            result = inject_block(
                session_id, store,
                max_chars=data.get("max_chars", MAX_CHARS),
                top_k=data.get("top_k", TOP_K),
                query=data.get("query") or data.get("user_text") or "",
                mode=_recall_mode(store, data.get("mode")),
                rerank_config=(data.get("rerank_config")
                               or _rerank_config_from(
                                   _load_runtime_config(store).get("rerank"))))
            return {"ok": True, **result}
        if action == "configure":
            return configure(data)
        return {"ok": False, "reason": "unknown_action", "action": action}
    except Exception as exc:  # CLI must always answer one JSON line
        return {"ok": False, "reason": "pipeline_error",
                "detail": type(exc).__name__ + ": " + str(exc)[:200]}


def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:])
    action = args[0] if args else ""
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except Exception:
        raw = ""
    payload = {}
    if raw.strip():
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    if action:
        payload.setdefault("action", action)
    result = dispatch(action or payload.get("action"), payload)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
