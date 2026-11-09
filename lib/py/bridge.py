# -*- coding: utf-8 -*-
"""Node <-> dsh_adapter bridge for dsh-biocore-ui (subprocess only).

The plugin never imports Python into Node: it spawns this file, which imports
the read-only dsh_adapter package and prints one JSON line per invocation.

Commands:
  default                          -> {"ok": true, "state": <adapter.default_state()>}
  ensure <state_file> [profile]    -> lazy-create / apply persona baselines
  turn <state_file> <text> [profile] -> one_turn + persona application + summary
  persona_compile <json> <md>      -> compile stdin persona text -> AstrBot profile

Nothing in dsh_adapter is imported for its side effects and no adapter file is
modified; load_state / one_turn / save_state are called as-is.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

# Package root: <pkg>/lib/py/bridge.py -> <pkg>; the python core ships inside
# <pkg>/python so an npm install works without the AstrBot checkout.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_PY = _PACKAGE_ROOT / "python"
_ADAPTER_DIR = Path(os.environ.get("BIOCORE_ADAPTER_DIR")
                    or (_BUNDLED_PY / "dsh_adapter"))
_PLUGIN_DIR = Path(os.environ.get("BIOCORE_PLUGIN_DIR") or _BUNDLED_PY)
_MIND_DIR = Path(os.environ.get("BIOCORE_MIND_DIR") or _BUNDLED_PY)
sys.path.insert(0, str(_ADAPTER_DIR))
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_MIND_DIR) not in sys.path:
    sys.path.insert(0, str(_MIND_DIR))

PERSONA_TEXT_LIMIT = 20000
PERSONA_FIELDS = (
    "O", "C", "E", "A", "N", "stress_reactivity", "recovery_rate",
    "social_baseline", "energy_baseline", "curiosity_baseline",
    "safety_baseline", "da_baseline", "oa_baseline", "ht_baseline",
    "autonomy_gain", "baseline_v", "baseline_a", "learning_rate",
)

try:
    import adapter  # noqa: E402  (read-only import from dsh_adapter)
except Exception as error:  # pragma: no cover - reported to Node as JSON
    print(json.dumps({"ok": False, "reason": "adapter_import_failed",
                      "detail": str(error)[:300]}, ensure_ascii=False))
    raise SystemExit(0)


def _num(value, fallback=0.0):
    try:
        return float(value)
    except Exception:
        return float(fallback)


def _clip(value, low=0.0, high=1.0):
    return max(low, min(high, _num(value, low)))


def _summary(state):
    core = state.get("core") if isinstance(state, dict) else {}
    core = core if isinstance(core, dict) else {}
    fields = ("v", "a", "social", "stress", "energy", "social_pain", "self_esteem")
    return {key: round(_num(core.get(key), 0.0), 3) for key in fields}


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _persona_profile(profile_path):
    """Load an AstrBot-style persona_profile.json -> flat numeric profile."""
    if not profile_path:
        return {}
    try:
        doc = json.loads(Path(str(profile_path)).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(doc, dict):
        return {}
    raw = doc.get("profile") if isinstance(doc.get("profile"), dict) else doc
    out = {}
    for key in PERSONA_FIELDS:
        if key in raw:
            out[key] = _num(raw.get(key), 0.0)
    if not out:
        return {}
    out["version"] = str(raw.get("version") or doc.get("persona_hash") or "")
    return out


def _persona_version(profile):
    if not profile:
        return ""
    return str(profile.get("version") or hashlib.sha256(
        json.dumps(profile, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12])


def _apply_persona(state, profile):
    """Whitelisted persona -> adapter state (traits -> affect -> base)."""
    if not isinstance(profile, dict) or not profile:
        return state
    if not isinstance(state.get("core"), dict):
        state["core"] = {}
    core = state["core"]
    recovery = _clip(profile.get("recovery_rate", 0.7), 0.0, 1.0)
    core["v"] = round(_clip(profile.get("baseline_v", 0.05), -1.0, 1.0), 3)
    core["a"] = round(_clip(profile.get("baseline_a", 0.4), 0.0, 1.0), 3)
    core["social"] = round(_clip(profile.get("social_baseline", 0.55), 0.0, 1.0), 3)
    core["energy"] = round(_clip(profile.get("energy_baseline", 0.55), 0.0, 1.0), 3)
    factor = round(1.25 - 0.50 * recovery, 3)
    core["valence_decay_minutes"] = round(120.0 * factor, 1)
    core["arousal_decay_minutes"] = round(30.0 * factor, 1)
    core["social_decay_minutes"] = round(180.0 * factor, 1)
    state["persona_version"] = _persona_version(profile)
    state["persona_profile"] = dict(profile)
    return state


def _persist_persona(state_file, profile, version):
    """adapter.save_state drops unknown keys; keep the persona link ourselves."""
    if not profile:
        return
    path = Path(state_file)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            doc = {}
        doc["persona_profile"] = dict(profile)
        doc["persona_version"] = str(version or "")
        _write_json_file(path, doc)
    except Exception:
        pass


def _compile_profile(text):
    if str(_MIND_DIR) not in sys.path:
        sys.path.insert(0, str(_MIND_DIR))
    try:  # optional: bundled builds ship the deterministic compiler only
        from mindbase.persona_compile import compile_persona  # noqa: E402
    except Exception:  # pragma: no cover - persona compile degrades
        compile_persona = None
    from mindbase.persona_bridge import normalize  # noqa: E402
    persona = compile_persona(text)
    return normalize(persona.numbers())


def _compile_persona(profile_path, md_path, text):
    raw = str(text or "").strip()
    # DSH prompt text is parsed as a template: turn {{...}} into (...) at
    # compile time so neither the preset nor the per-turn block can trip it.
    converted = raw.count("{{") + raw.count("}}")
    raw = raw.replace("{{", "(").replace("}}", ")").strip()
    if not raw:
        return _emit({"ok": False, "reason": "empty_text"})
    if len(raw) > PERSONA_TEXT_LIMIT:
        return _emit({"ok": False, "reason": "text_too_long",
                      "limit": PERSONA_TEXT_LIMIT})
    try:
        profile = _compile_profile(raw)
    except Exception as error:
        return _emit({"ok": False, "reason": "compile_failed",
                      "detail": str(error)[:300]})
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    payload = {
        "persona": raw,
        "profile": profile,
        "persona_hash": digest,
        "compiled_at": time.time(),
        "compiler": "mindbase.persona_compile",
        "placeholders_converted": converted,
    }
    profile_path = Path(str(profile_path))
    md_path = Path(str(md_path))
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_file(profile_path, payload)
    md_path.write_text(raw + "\n", encoding="utf-8")
    return _emit({"ok": True, "profile": profile,
                  "persona_hash": digest,
                  "updated_at": round(time.time() * 1000),
                  "compiler": payload["compiler"]})


def _ensure(state_file, profile_path=""):
    path = Path(state_file)
    created = not path.exists()
    stored = _read_json_file(path) if path.exists() else {}
    state = adapter.load_state(path)
    profile = _persona_profile(profile_path)
    version = _persona_version(profile)
    if profile and str(stored.get("persona_version") or "") != version:
        _apply_persona(state, profile)
    if created or profile:
        adapter.save_state(state, path)
    if profile:
        _persist_persona(path, profile, version)
    return _emit({"ok": True, "created": created,
                  "state_file": str(path), "state": _summary(state),
                  "persona_applied": bool(profile)})


def _turn(state_file, text, profile_path=""):
    path = Path(state_file)
    stored = _read_json_file(path) if path.exists() else {}
    state = adapter.load_state(path)
    profile = _persona_profile(profile_path)
    version = _persona_version(profile)
    if profile and str(stored.get("persona_version") or "") != version:
        _apply_persona(state, profile)
    result = adapter.one_turn(str(text or "")[:2000], state)
    adapter.save_state(result["state_after"], path)
    if profile:
        _persist_persona(path, profile, version)
    hint = result.get("reply_hint") or {}
    debug = result.get("debug") or {}
    return _emit({
        "ok": True,
        "state_file": str(path),
        "before": _summary(state),
        "after": _summary(result["state_after"]),
        "deltas": debug.get("deltas") or {},
        "kind": debug.get("kind"),
        "persona_applied": bool(profile),
        "reply_hint": {
            "length_scale": hint.get("length_scale"),
            "initiative": hint.get("initiative"),
            "tone": hint.get("tone"),
            "allow_correction": hint.get("allow_correction"),
            "sources": hint.get("sources") or [],
        },
    })


def _pipeline():
    """Import the host-neutral policy module from the plugin checkout."""
    plugin_dir = str(_PLUGIN_DIR)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    from biocore_runtime import pipeline  # noqa: E402
    return pipeline


def _json_arg(value):
    try:
        return json.loads(value) if value else {}
    except Exception:
        return {}


_SUMMARIZER_STORE = [""]


def _deepseek_key():
    """API key for the summariser: env first, then the DSH credential store."""
    key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    try:
        import re
        text = open(str(_DSH_HOME / ".credentials.yaml"), encoding="utf-8").read()
        hit = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", text)
        if hit:
            return hit.group(1).strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _summarizer_config(store):
    """The summariser block of the store's core_config.json (no silent default)."""
    for name in ("core_config.json", "config.json"):
        path = os.path.join(str(store or ""), name)
        if not os.path.isfile(path):
            continue
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            continue
        block = data.get("summarizer") if isinstance(data, dict) else None
        if isinstance(block, dict) and str(block.get("model") or "").strip():
            return {"base_url": str(block.get("base_url") or block.get("baseUrl")
                                    or "https://api.deepseek.com/v1"),
                    "model": str(block["model"]).strip(),
                    "key": str(block.get("key") or block.get("api_key") or "").strip()}
    raise RuntimeError("summarizer config missing: core_config.json has no summarizer block "
                       "(the event layer cannot close events without it)")


def _llm_summarizer(prompt):
    """Event-close summariser for the real plugin path.

    capture() only closes events when it is handed a summariser; without one the
    event layer buffers forever and no event ever exists (the production path had
    exactly that hole). This uses the host's own model over HTTPS.
    """
    cfg = _summarizer_config(_SUMMARIZER_STORE[0])
    key = cfg["key"] or _deepseek_key()
    if not key:
        raise RuntimeError("summarizer config has no key and DEEPSEEK_API_KEY is unset")
    import urllib.request
    model = cfg["model"]
    base = cfg["base_url"]
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": "只输出 JSON 对象，不要解释。"},
                     {"role": "user", "content": str(prompt)}],
    }).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    except Exception as exc:
        sys.stderr.write("__bioSummarizer failed: %s\n" % str(exc)[:160])
        return None


def _memory_capture(argv):
    store = argv[1]
    session_id = argv[2]
    role = argv[3] if len(argv) > 3 else "user"
    text = argv[4] if len(argv) > 4 else ""
    config = _json_arg(argv[5] if len(argv) > 5 else "")
    config = dict(config) if isinstance(config, dict) else {}
    _SUMMARIZER_STORE[0] = str(store or "")
    if not config.get("event_summarizer"):
        # the host never supplied one; without it no event is ever generated.
        # _llm_summarizer raises when core_config.json has no summarizer block.
        _summarizer_config(store)
        config["event_summarizer"] = _llm_summarizer
    result = _pipeline().capture(session_id, role, text, store, config)
    decision = result.get("decision") if isinstance(result, dict) else {}
    decision = decision if isinstance(decision, dict) else {}
    entry = result.get("entry") if isinstance(result, dict) else {}
    entry = entry if isinstance(entry, dict) else {}
    return _emit({
        "ok": True,
        "captured": bool(decision.get("capture")),
        "reason": str(decision.get("reason") or ""),
        "entry_id": str(entry.get("id") or ""),
    })


def _memory_inject(argv):
    return _memory_inject_impl(argv)


def _memory_inject_impl(argv):
    store = argv[1]
    session_id = argv[2]
    kwargs = {}
    if len(argv) > 3 and str(argv[3]).strip():
        kwargs["max_chars"] = int(argv[3])
    if len(argv) > 4 and str(argv[4]).strip():
        kwargs["top_k"] = int(argv[4])
    result = _pipeline().inject_block(session_id, store, **kwargs)
    return _emit({"ok": True, **result})


def _memory_tool(argv):
    store = argv[1]
    session_id = argv[2]
    name = argv[3] if len(argv) > 3 else ""
    args = _json_arg(argv[4] if len(argv) > 4 else "")
    result = _pipeline().tool_call(name, args, {"root": store},
                                   session_id=session_id)
    if not isinstance(result, dict):
        result = {"reason": "tool_failed"}
    # Flat contract: outer ok, result without the inner ok.
    inner_ok = bool(result.get("ok"))
    flat = {key: value for key, value in result.items() if key != "ok"}
    return _emit({"ok": inner_ok, "result": flat})


def _runtime_prompt(argv):
    payload = _json_arg(argv[1] if len(argv) > 1 else "")
    return _emit(_pipeline().prompt_block(payload))


def _runtime_gate(argv):
    payload = _json_arg(argv[1] if len(argv) > 1 else "")
    reply = payload.get("reply", payload.get("reply_text", ""))
    return _emit(_pipeline().gate_reply(reply, payload))


_EVENT_ACTIONS = ("memory_capture", "memory_inject")


def _runtime_action(argv):
    action = argv[1] if len(argv) > 1 else ""
    payload = _json_arg(argv[2] if len(argv) > 2 else "")
    if action in _EVENT_ACTIONS and isinstance(payload, dict):
        # the real node path calls dispatch(); it must carry the summariser too,
        # or the event layer stays silently off (capture) / raises (render)
        payload = dict(payload)
        store = str(payload.get("store") or payload.get("root") or "")
        _SUMMARIZER_STORE[0] = store
        if str(os.environ.get("BIOCORE_STRICT_TS") or "") == "1" \
                and action == "memory_capture" and not payload.get("now"):
            raise RuntimeError("capture payload has no logical timestamp (now); "
                               "BIOCORE_STRICT_TS forbids falling back to the wall clock")
        if not payload.get("event_summarizer"):
            _summarizer_config(store)
            payload["event_summarizer"] = _llm_summarizer
    return _emit(_pipeline().dispatch(action, payload))


def _read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json_file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _proactive_tick(argv):
    """task-205 follow-up: decision lives in biocore_runtime.pipeline."""
    payload = _json_arg(argv[1] if len(argv) > 1 else "")
    return _emit(_pipeline().dispatch("proactive_tick", payload))


def main(argv):
    if not argv:
        return _emit({"ok": False, "reason": "missing_command"})
    command = argv[0]
    try:
        if command == "default":
            return _emit({"ok": True, "state": adapter.default_state()})
        if command == "ensure" and len(argv) >= 2:
            return _ensure(argv[1], argv[2] if len(argv) > 2 else "")
        if command == "turn" and len(argv) >= 3:
            return _turn(argv[1], argv[2], argv[3] if len(argv) > 3 else "")
        if command == "persona_compile" and len(argv) >= 3:
            return _compile_persona(argv[1], argv[2], sys.stdin.buffer.read().decode("utf-8", "replace"))
        if command == "memory_capture" and len(argv) >= 5:
            return _memory_capture(argv)
        if command == "memory_inject" and len(argv) >= 3:
            return _memory_inject(argv)
        if command == "memory_tool" and len(argv) >= 4:
            return _memory_tool(argv)
        if command == "runtime_prompt" and len(argv) >= 2:
            return _runtime_prompt(argv)
        if command == "runtime_gate" and len(argv) >= 2:
            return _runtime_gate(argv)
        if command == "runtime_action" and len(argv) >= 2:
            return _runtime_action(argv)
        if command == "proactive_tick" and len(argv) >= 2:
            return _proactive_tick(argv)
        return _emit({"ok": False, "reason": "bad_args", "argv": argv[:3]})
    except Exception as error:
        return _emit({"ok": False, "reason": "adapter_error",
                      "detail": str(error)[:300]})


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
