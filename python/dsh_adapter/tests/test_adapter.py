# -*- coding: utf-8 -*-
"""Tests for the DSH adapter: state IO, one-turn update, constraints, purity."""
import ast
import builtins
import copy
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import adapter  # noqa: E402


class StateIOTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "nested" / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_state_shape(self):
        state = adapter.default_state()
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["turn"], 0)
        for key in ("v", "a", "d", "social", "stress", "energy"):
            self.assertIn(key, state["core"])
        self.assertEqual(state["relationship"]["closeness"], 0.5)
        self.assertEqual(state["relationship"]["trust"], 0.5)
        self.assertEqual(state["relationship"]["conflict"], 0.0)
        self.assertEqual(state["seen"], {})

    def test_load_missing_file_returns_default(self):
        loaded = adapter.load_state(self.store)
        self.assertEqual(loaded, adapter.default_state())

    def test_save_creates_parent_and_roundtrips(self):
        state = adapter.default_state()
        state["turn"] = 2
        state["core"]["v"] = -0.3
        saved = adapter.save_state(state, self.store)
        self.assertTrue(Path(saved).exists())
        self.assertTrue(self.store.parent.is_dir())
        loaded = adapter.load_state(self.store)
        self.assertEqual(loaded["turn"], 2)
        self.assertAlmostEqual(loaded["core"]["v"], -0.3, places=4)

    def test_save_leaves_no_tmp_file(self):
        adapter.save_state(adapter.default_state(), self.store)
        leftovers = list(self.store.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_malformed_json_raises(self):
        self.store.parent.mkdir(parents=True)
        self.store.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(ValueError):
            adapter.load_state(self.store)

    def test_non_object_json_raises(self):
        self.store.parent.mkdir(parents=True)
        self.store.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(ValueError):
            adapter.load_state(self.store)

    def test_load_accepts_dict(self):
        loaded = adapter.load_state({"turn": 3, "core": {"v": 0.4}})
        self.assertEqual(loaded["turn"], 3)
        self.assertAlmostEqual(loaded["core"]["v"], 0.4, places=4)

    def test_normalize_fills_defaults_and_clamps(self):
        state = adapter.normalize_state({
            "turn": 2,
            "core": {"v": 9, "a": -1},
            "relationship": {"closeness": -3},
        })
        self.assertEqual(state["turn"], 2)
        self.assertEqual(state["core"]["v"], 1.0)
        self.assertEqual(state["core"]["a"], 0.0)
        self.assertEqual(state["core"]["stress"], 0.2)  # default filled in
        self.assertEqual(state["relationship"]["closeness"], 0.0)

    def test_save_does_not_mutate(self):
        state = adapter.default_state()
        before = copy.deepcopy(state)
        adapter.save_state(state, self.store)
        self.assertEqual(state, before)


class OneTurnTest(unittest.TestCase):
    def setUp(self):
        self.state = adapter.default_state()

    def test_result_shape_and_hint_contract(self):
        result = adapter.one_turn("嗯", self.state)
        for key in ("reply_hint", "state_after", "prompt_blocks", "debug"):
            self.assertIn(key, result)
        hint = result["reply_hint"]
        for key in ("length_scale", "initiative", "tone",
                    "allow_correction", "note", "sources"):
            self.assertIn(key, hint)
        self.assertGreaterEqual(hint["length_scale"], 0.5)
        self.assertLessEqual(hint["length_scale"], 1.5)
        self.assertGreaterEqual(hint["initiative"], 0.0)
        self.assertLessEqual(hint["initiative"], 1.0)
        self.assertIsInstance(hint["tone"], str)
        self.assertIsInstance(hint["allow_correction"], bool)
        self.assertIsInstance(hint["sources"], list)

    def test_advances_turn_and_injected_time(self):
        result = adapter.one_turn("嗯", self.state)
        after = result["state_after"]
        self.assertEqual(after["turn"], 1)
        self.assertAlmostEqual(after["now"], adapter.TURN_SECONDS)
        self.assertAlmostEqual(after["core"]["last_bio_tick_ts"],
                               adapter.TURN_SECONDS)

    def test_state_after_has_tick_output(self):
        after = adapter.one_turn("嗯", self.state)["state_after"]
        self.assertIsInstance(after["core"]["needs"], dict)
        self.assertIsInstance(after["core"]["intero"], dict)

    def test_input_state_is_not_mutated(self):
        before = copy.deepcopy(self.state)
        adapter.one_turn("今天好累", self.state)
        self.assertEqual(self.state, before)

    def test_same_input_is_deterministic(self):
        first = adapter.one_turn("谢谢你", self.state)
        second = adapter.one_turn("谢谢你", self.state)
        self.assertEqual(first["reply_hint"], second["reply_hint"])
        self.assertEqual(first["state_after"], second["state_after"])

    def test_praise_moves_affect_positive(self):
        before = copy.deepcopy(self.state["core"])
        result = adapter.one_turn("谢谢你，今天好多了", self.state)
        after = result["state_after"]["core"]
        self.assertEqual(result["debug"]["kind"], "praise")
        self.assertGreater(after["v"], before["v"])
        self.assertGreater(after["social"], before["social"])

    def test_insult_raises_social_pain(self):
        result = adapter.one_turn("滚，烦死了", self.state)
        after = result["state_after"]["core"]
        self.assertEqual(result["debug"]["kind"], "insult")
        self.assertGreater(after["social_pain"], 0.0)

    def test_affection_raises_social(self):
        result = adapter.one_turn("抱抱", self.state)
        after = result["state_after"]["core"]
        self.assertEqual(result["debug"]["kind"], "affection")
        self.assertGreater(after["social"], self.state["core"]["social"])

    def test_sad_text_uses_empathic_burden(self):
        result = adapter.one_turn("今天有点累，很难受", self.state)
        self.assertEqual(result["debug"]["kind"], "sad")
        self.assertIn("stress", result["debug"]["deltas"])

    def test_negative_valence_shortens_length(self):
        warm = adapter.one_turn("嗯", self.state)["reply_hint"]
        cold_state = adapter.normalize_state({"core": {"v": -0.8}})
        cold = adapter.one_turn("嗯", cold_state)["reply_hint"]
        self.assertLessEqual(cold["length_scale"], warm["length_scale"])

    def test_prompt_blocks_render(self):
        result = adapter.one_turn("嗯", self.state)
        blocks = result["prompt_blocks"]
        self.assertTrue(blocks)
        for block in blocks:
            self.assertIn(block["position"],
                          {"before_history", "depth", "after_history"})
            self.assertTrue(block["content"].strip())
        joined = "\n".join(block["content"] for block in blocks)
        self.assertIn("关系", joined)
        self.assertTrue(result["debug"]["state_block"])

    def test_seen_is_bounded(self):
        state = adapter.default_state()
        state["seen"] = {"k%d" % i: float(i) for i in range(adapter.SEEN_LIMIT + 60)}
        after = adapter.one_turn("嗯", state)["state_after"]
        self.assertLessEqual(len(after["seen"]), adapter.SEEN_LIMIT)
        self.assertTrue(after["seen"])

    def test_tolerates_none_and_garbage_state(self):
        for bad in (None, "not a state", 42, []):
            result = adapter.one_turn("嗯", bad)
            self.assertIn("reply_hint", result)

    def test_one_turn_is_pure_no_io_no_clock(self):
        state = adapter.default_state()
        real = {
            "open": builtins.open,
            "path_open": pathlib.Path.open,
            "read_text": pathlib.Path.read_text,
            "write_text": pathlib.Path.write_text,
            "replace": os.replace,
            "time": time.time,
        }

        def boom(*args, **kwargs):
            raise AssertionError("one_turn must not touch IO or the clock")

        builtins.open = boom
        pathlib.Path.open = boom
        pathlib.Path.read_text = boom
        pathlib.Path.write_text = boom
        os.replace = boom
        time.time = boom
        try:
            result = adapter.one_turn("谢谢你", state)
        finally:
            builtins.open = real["open"]
            pathlib.Path.open = real["path_open"]
            pathlib.Path.read_text = real["read_text"]
            pathlib.Path.write_text = real["write_text"]
            os.replace = real["replace"]
            time.time = real["time"]
        self.assertIn("reply_hint", result)


class ConstraintTest(unittest.TestCase):
    def test_merged_sources_and_confidence(self):
        hint = adapter.one_turn("嗯", adapter.default_state())["reply_hint"]
        for source in ("exit_or_silence", "metacog", "state"):
            self.assertIn(source, hint["sources"])
        self.assertGreaterEqual(hint["confidence"], 0.05)
        self.assertLessEqual(hint["confidence"], 0.95)

    def test_classify_text(self):
        cases = {
            "滚": "insult",
            "谢谢你": "praise",
            "抱抱": "affection",
            "今天有点累": "sad",
            "不想聊了": "reject",
            "嗯": "reply",
        }
        for text, expected in cases.items():
            self.assertEqual(adapter.classify_user_text(text), expected, text)

    def test_no_astrbot_or_main_import(self):
        root = HERE.parent
        for name in ("adapter.py", "cli.py"):
            path = root / name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    head = module.split(".")[0]
                    self.assertNotIn(head, ("astrbot", "main", "aiocqhttp"),
                                     "%s imports %s" % (name, module))
            self.assertNotIn("import astrbot", source)

    def test_adapter_lives_outside_plugin_repo(self):
        here = str(Path(adapter.__file__).resolve())
        self.assertIn("dsh_adapter", here)
        self.assertNotIn("astrbot_plugin_biocore_standalone", here)

    def test_cli_module_imports(self):
        import cli
        self.assertTrue(callable(cli.main))
        self.assertTrue(cli.DEMO_TEXTS)


class UIProjectionTest(unittest.TestCase):
    """DSH-UI-DESIGN section 7: 57 rows, endpoint payloads, 4 switches."""

    @staticmethod
    def _resolve(payload, path):
        current = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise KeyError(path)
            current = current[part]
        return current

    def _turned_state(self, turns=1):
        state = adapter.default_state()
        state["session_id"] = "sess-1"
        for text in ("谢谢你", "今天有点累", "抱抱", "嗯")[:turns]:
            state = adapter.one_turn(text, state)["state_after"]
        return state

    def test_six_endpoints_are_declared(self):
        self.assertEqual(len(adapter.ENDPOINTS), 6)
        self.assertEqual(set(adapter.ENDPOINTS), {
            "/api/biocore/state", "/api/biocore/series",
            "/api/biocore/memory", "/api/biocore/trace",
            "/api/biocore/health", "/api/biocore/switch"})

    def test_field_paths_are_57(self):
        self.assertEqual(len(adapter.FIELD_PATHS), 57)
        self.assertEqual(len(set(adapter.FIELD_PATHS)), 57)

    def test_state_payload_has_every_documented_row(self):
        payload = adapter.state_payload(self._turned_state(), session_id="sess-1")
        for path in adapter.FIELD_PATHS:
            self._resolve(payload, path)
        self.assertEqual(payload["adapter"]["level"], "L2")
        self.assertEqual(payload["session_id"], "sess-1")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["available"])

    def test_first_screen_five_numbers_and_three_words(self):
        payload = adapter.state_payload(self._turned_state(), session_id="sess-1")
        self.assertGreaterEqual(payload["v"], -1.0)
        self.assertLessEqual(payload["v"], 1.0)
        for name in ("tension", "warmth", "heaviness", "clarity"):
            value = payload["intero"][name]
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        self.assertIn(payload["tone"]["label"],
                      ("", "先接住情绪", "慢一点说", "亲近一点", "没听清先确认"))
        self.assertIn(payload["relation"]["stage"], ("陌生", "熟悉", "亲近", "稳定"))
        self.assertIn(payload["autonomy"]["state"], ("静", "等", "想联系", "冷却"))

    def test_state_payload_size_limit(self):
        state = self._turned_state()
        state["memory"]["recent"] = [
            {"id": "m%d" % i, "ts": 1.0, "score": 0.5, "source": "chat",
             "kind": "fact", "text": "很长的记忆正文" * 20} for i in range(20)]
        payload = adapter.state_payload(state, session_id="sess-1")
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(size, adapter.STATE_PAYLOAD_LIMIT)
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("prompt_blocks", blob)

    def test_series_payload_contract(self):
        state = self._turned_state(4)
        payload = adapter.series_payload(state, session_id="sess-1", window="1h")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["available"])
        self.assertEqual(payload["window"], "1h")
        for name in ("v", "tension", "warmth", "heaviness", "clarity"):
            points = payload["series"][name]
            self.assertTrue(points)
            self.assertLessEqual(len(points), adapter.SERIES_LIMIT)
            for point in points:
                self.assertIn("ts", point)
                self.assertIn("value", point)
        bad_window = adapter.series_payload(state, window="9y")
        self.assertEqual(bad_window["window"], "1h")

    def test_memory_payload_preview_and_limit(self):
        state = self._turned_state()
        state["memory"]["recent"] = [
            {"id": "m%d" % i, "ts": float(i), "score": 0.9, "source": "chat",
             "kind": "fact", "text": "长" * 200} for i in range(30)]
        state["memory"]["count"] = 30
        state["memory"]["last_recall"] = {
            "query": "体检", "hits": 3, "latency_ms": 12.5}
        payload = adapter.memory_payload(state, session_id="sess-1")
        self.assertTrue(payload["available"])
        self.assertEqual(len(payload["recent"]), adapter.MEMORY_LIMIT)
        for item in payload["recent"]:
            self.assertLessEqual(len(item["text_preview"]),
                                 adapter.MEMORY_PREVIEW_LIMIT)
        self.assertEqual(payload["last_recall"]["query"], "体检")
        self.assertEqual(payload["last_recall"]["hits"], 3)
        small = adapter.memory_payload(state, limit=5)
        self.assertEqual(len(small["recent"]), 5)

    def test_trace_payload_contract(self):
        state = self._turned_state()
        payload = adapter.trace_payload(state, session_id="sess-1")
        self.assertTrue(payload["available"])
        trace = payload["trace"]
        self.assertEqual(set(trace), {"state_delta", "intent", "metacog_band",
                                      "length_source", "prompt_diff"})
        self.assertIsNone(adapter.state_payload(state)["trace"])
        with_trace = adapter.state_payload(state, include_trace=True)
        self.assertIsNotNone(with_trace["trace"])

    def test_switch_map_has_exactly_four_keys(self):
        self.assertEqual(set(adapter.SWITCH_MAP), {
            "safety.master", "safety.boundary", "safety.crisis",
            "autonomy.proactive_send"})
        for key, config_key in adapter.SWITCH_MAP.items():
            self.assertIn(config_key, adapter.DEFAULT_SETTINGS)
            request = adapter.switch_request(
                {"key": key, "value": True, "confirm": key})
            self.assertTrue(request["ok"], key)
            self.assertEqual(request["settings_patch"], {config_key: True})

    def test_switch_rejects_unknown_invalid_and_missing_confirm(self):
        bad_key = adapter.switch_request(
            {"key": "core.v", "value": 0.9, "confirm": "core.v"})
        self.assertFalse(bad_key["ok"])
        self.assertEqual(bad_key["code"], "unknown_key")
        bad_value = adapter.switch_request(
            {"key": "safety.master", "value": "on", "confirm": "safety.master"})
        self.assertFalse(bad_value["ok"])
        self.assertEqual(bad_value["code"], "invalid_value")
        no_confirm = adapter.switch_request(
            {"key": "safety.master", "value": True})
        self.assertFalse(no_confirm["ok"])
        self.assertEqual(no_confirm["code"], "confirm_required")
        wrong_confirm = adapter.switch_request(
            {"key": "safety.master", "value": True, "confirm": "safety.boundary"})
        self.assertFalse(wrong_confirm["ok"])

    def test_apply_switch_only_touches_settings(self):
        state = adapter.default_state()
        before = copy.deepcopy(state)
        result = adapter.apply_switch(
            state, {"key": "safety.master", "value": True,
                    "confirm": "safety.master"})
        self.assertTrue(result["ok"])
        after = result["state_after"]
        self.assertTrue(after["settings"]["safety_enabled"])
        self.assertEqual(after["core"], before["core"])
        self.assertEqual(after["memory"], before["memory"])
        self.assertEqual(after["relationship"], before["relationship"])
        self.assertEqual(result["settings_patch"], {"safety_enabled": True})
        self.assertEqual(state, before)  # input untouched

    def test_unknown_session_returns_no_state_yet(self):
        payload = adapter.state_payload(None, session_id="missing")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["available"])
        self.assertEqual(payload["reason"], "no_state_yet")
        for path in adapter.FIELD_PATHS:
            self._resolve(payload, path)
        state = self.default_with_session("session-a")
        mismatch = adapter.state_payload(state, session_id="session-b")
        self.assertFalse(mismatch["available"])
        self.assertEqual(mismatch["reason"], "no_state_yet")

    @staticmethod
    def default_with_session(session_id):
        state = adapter.default_state()
        state["session_id"] = session_id
        return state

    def test_health_payload_with_and_without_state(self):
        empty = adapter.health_payload(None)
        self.assertFalse(empty["available"])
        self.assertTrue(empty["degraded"])
        full = adapter.health_payload(self._turned_state())
        self.assertTrue(full["available"])
        for key in ("service", "store", "last_tick_ts", "tick_interval_s"):
            self.assertIn(key, full)

    def test_projections_isolate_sessions(self):
        state = self.default_with_session("session-a")
        state = adapter.one_turn("嗯", state)["state_after"]
        for builder in (adapter.series_payload, adapter.memory_payload,
                        adapter.trace_payload):
            payload = builder(state, session_id="session-b")
            self.assertFalse(payload["available"])
            self.assertEqual(payload["reason"], "no_state_yet")
        self.assertTrue(adapter.trace_payload(
            state, session_id="session-a")["available"])

    def test_payload_builders_are_pure(self):
        state = self._turned_state()
        real_open = builtins.open
        real_read = pathlib.Path.read_text
        real_replace = os.replace

        def boom(*args, **kwargs):
            raise AssertionError("payload builders must not touch the filesystem")

        builtins.open = boom
        pathlib.Path.read_text = boom
        os.replace = boom
        try:
            adapter.state_payload(state, session_id="sess-1")
            adapter.series_payload(state)
            adapter.memory_payload(state)
            adapter.trace_payload(state)
            adapter.health_payload(state)
            adapter.switch_request(
                {"key": "safety.master", "value": True,
                 "confirm": "safety.master"})
        finally:
            builtins.open = real_open
            pathlib.Path.read_text = real_read
            os.replace = real_replace


if __name__ == "__main__":
    unittest.main()
