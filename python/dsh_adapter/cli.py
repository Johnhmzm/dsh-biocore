# -*- coding: utf-8 -*-
"""CLI demo: run BioCore turns through the DSH adapter and print the result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapter

DEMO_TEXTS = (
    "谢谢你，今天好多了",
    "今天有点累，先不聊了",
    "抱抱",
)


def _print_core_diff(before, after):
    old = adapter.state_summary(before)["core"]
    new = adapter.state_summary(after)["core"]
    changed = False
    for key, value in old.items():
        target = new.get(key, value)
        if abs(target - value) >= 0.0005:
            changed = True
            print("  %-12s %6.3f -> %6.3f  (%+.3f)"
                  % (key, value, target, target - value))
    if not changed:
        print("  (no numeric change)")


def _print_blocks(blocks):
    for block in blocks:
        text = block["content"].replace("\n", " / ")
        print("  [%s] %s" % (block["position"], text))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="BioCore DSH adapter demo (no AstrBot, no LLM).")
    parser.add_argument("--state", default=str(Path(__file__).resolve().parent
                                              / "data" / "state.json"),
                        help="state JSON path owned by the caller")
    parser.add_argument("--text", action="append",
                        help="user text for one turn; repeat for multiple turns")
    parser.add_argument("--reset", action="store_true",
                        help="ignore the existing state file and start fresh")
    parser.add_argument("--no-save", action="store_true",
                        help="do not write the state back")
    parser.add_argument("--now", type=float, default=None,
                        help="injected timestamp for the first turn")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    store = Path(args.state)
    state = adapter.default_state() if args.reset else adapter.load_state(store)
    state["session_id"] = str(state.get("session_id") or "cli-demo")
    texts = args.text or list(DEMO_TEXTS)
    print("state:", store, "| turn:", state.get("turn"), "| now:", state.get("now"))
    if args.reset:
        print("(reset: fresh default state)")

    for index, text in enumerate(texts, start=1):
        before = state
        result = adapter.one_turn(text, state, now=args.now if index == 1 else None)
        state = result["state_after"]
        hint = result["reply_hint"]
        debug = result["debug"]
        print("\n== turn %d  input=%r" % (index, text))
        print("classifier kind: %s | deltas: %s"
              % (debug["kind"], json.dumps(debug["deltas"], ensure_ascii=False)))
        print("state diff:")
        _print_core_diff(before, state)
        print("constraints: "
              + json.dumps({key: hint.get(key) for key in
                            ("length_scale", "initiative", "tone",
                             "allow_correction", "sources")},
                           ensure_ascii=False))
        print("tick:", json.dumps(debug["tick"], ensure_ascii=False))
        print("prompt blocks:")
        _print_blocks(result["prompt_blocks"])
        payload = adapter.state_payload(state, session_id=state["session_id"])
        print("ui payload: rows=%d available=%s degraded=%s"
              % (len(adapter.FIELD_PATHS), payload["available"],
                 payload["degraded"]))
        print("first screen: v=%.2f tension=%.2f warmth=%.2f heaviness=%.2f"
              " clarity=%.2f | tone=%s relation=%s autonomy=%s"
              % (payload["v"], payload["intero"]["tension"],
                 payload["intero"]["warmth"], payload["intero"]["heaviness"],
                 payload["intero"]["clarity"], payload["tone"]["label"] or "正常",
                 payload["relation"]["stage"], payload["autonomy"]["state"]))

    if not args.no_save:
        saved = adapter.save_state(state, store)
        print("\nsaved:", saved)
    else:
        print("\n(not saved: --no-save)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
