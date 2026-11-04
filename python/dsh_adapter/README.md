# DSH adapter (BioCore outside AstrBot)

Minimal, dependency-light adapter that runs one BioCore turn in DSH:
state JSON -> one turn (state update + interpretation) -> expression
constraints + JSON state. It never imports AstrBot or the plugin main.py;
the plugin repo is only read (biocore_model is imported through a path
bootstrap, override with BIOCORE_MODEL_PATH).

## Files

- adapter.py  load_state / one_turn / save_state + state contract
- cli.py      command-line demo (prints state diff and constraints)
- tests/      unittest suite (state IO, update/write-back, merge, purity)

## Run

    cd deepseek/dsh_adapter
    python cli.py --reset --state data/demo_state.json
    python -m unittest discover -s tests -t . -v

The default demo runs three turns: praise, tired/withdraw, affection.

## Contract

    state = load_state(path)          # missing file -> fresh default
    result = one_turn(text, state)    # pure: no IO / clock / LLM
    state = save_state(result["state_after"], path)

one_turn returns:

    {
      "reply_hint": {                 # expression constraint only
        "length_scale": 0.5-1.5,
        "initiative": 0-1,
        "tone": "...",
        "allow_correction": bool,
        "note": "...",
        "sources": [...]
      },
      "state_after": {...},           # next state to persist
      "prompt_blocks": [              # preset blocks for the host prompt
        {"position": "before_history", "depth": 0, "role": "system",
         "content": "..."}
      ],
      "debug": {"kind", "deltas", "tick", "confidence", "state_block"}
    }

It never produces reply text, never calls a model, and never talks to the
network. Time is injected (now) or advanced deterministically by
TURN_SECONDS; file IO only happens in load_state / save_state.

## Reused pure modules (read-only)

- feedback_user: bounded user-behaviour deltas (praise / insult / reject ...)
- bio_tick: one life-maintenance tick (decay, interoception, needs)
- expr_state: deterministic state block text
- preset: structured prompt blocks
- x_directives: canonical merged expression constraints

## State JSON

    {
      "version": 1,
      "turn": 0,
      "now": 0.0,
      "core": {"v": 0.0, "a": 0.4, "d": 0.55, "social": 0.55, "stress": 0.2,
               "energy": 0.6, "sleep_debt": 0.0, "...": "..."},
      "relationship": {"closeness": 0.5, "trust": 0.5, "conflict": 0.0},
      "seen": {}
    }

seen holds feedback idempotency keys and is bounded to SEEN_LIMIT entries.
## DSH UI data shape (DSH-UI-DESIGN section 7)

adapter.py also projects the exact payload shapes the DSH UI expects. No HTTP
layer here: the host calls these builders from its routes.

- state_payload(state, session_id) -> /state: 57 documented rows (A-K),
  first screen = v + intero.tension/warmth/heaviness/clarity + tone.label
  + relation.stage + autonomy.state; FIELD_PATHS lists all 57 paths.
- series_payload(state, window) -> /series: 5 curves, at most 120 points.
- memory_payload(state, limit) -> /memory: at most 20 previews, each
  text_preview <= 80 chars.
- trace_payload(state) -> /trace: state_delta/intent/metacog_band/
  length_source/prompt_diff (trace stays out of /state by default).
- health_payload() -> /health: service/store/last_tick_ts/tick_interval_s.
- switch_request(body) / apply_switch(state, body) -> /switch: only the 4
  keys safety.master, safety.boundary, safety.crisis,
  autonomy.proactive_send; requires {key, value, confirm}; patches the
  settings section only (safety_enabled / boundary_enabled /
  crisis_safety_enabled / proactive_send).

Hard rules kept by the builder: no prompt text or full memory body, no
side-effect on GET (pure functions), session isolation (mismatch or missing
state -> available:false + reason no_state_yet), readable degraded output,
and /state payload stays under 16 KB.
