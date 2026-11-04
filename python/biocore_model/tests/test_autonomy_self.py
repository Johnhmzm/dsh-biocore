# -*- coding: utf-8 -*-
"""task-222: self layer (persona/narrative/drift) + autonomy decision/limits."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from biocore_runtime import autonomy, pipeline, self_layer


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.store = self.data_dir / "memory"
        self.persona = Path(self.tmp.name) / "missing.json"
        (self.store / "sessions").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, session_id="s1", **extra):
        data = {"store": str(self.store), "session_id": session_id,
                "persona_profile": str(self.persona)}
        data.update(extra)
        return data

    def configure(self, **extra):
        data = {"data_dir": str(self.data_dir)}
        data.update(extra)
        pipeline.dispatch("configure", data)

    def actions(self, rows):
        lines = []
        for row in rows:
            line = {"ts": time.time(), "kind": "write", "args": {},
                    "reason": "test", "result": {"ok": True}}
            line.update(row)
            lines.append(json.dumps(line, ensure_ascii=False))
        (self.data_dir / "actions.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")


class SelfLayerTest(Case):
    def test_persona_locked(self):
        self.configure(persona_text="我是安吉拉")
        doc = self_layer.update(self.store, self_layer.hash_persona("我是安吉拉"))
        self.assertTrue(doc["persona_locked"])
        summary = self_layer.summary(self.store)
        self.assertTrue(summary["persona_locked"])
        self.assertEqual(summary["drift_count"], 0)

    def test_drift_detected(self):
        self.configure(persona_text="旧人设")
        self_layer.update(self.store, self_layer.hash_persona("旧人设"))
        self_layer.update(self.store, self_layer.hash_persona("新人设"))
        summary = self_layer.summary(self.store)
        self.assertEqual(summary["drift_count"], 1)

    def test_same_hash_is_idempotent(self):
        h = self_layer.hash_persona("稳定人设")
        self_layer.update(self.store, h)
        self_layer.update(self.store, h)
        self.assertEqual(self_layer.summary(self.store)["drift_count"], 0)

    def test_narrative_accumulates(self):
        self_layer.update(self.store, "", {"kind": "warmth", "text": "谢谢你"})
        self_layer.update(self.store, "", {"kind": "repair", "text": "对不起"})
        summary = self_layer.summary(self.store)
        self.assertEqual(summary["narrative_count"], 2)
        self.assertEqual(summary["last_narrative"], "对不起")


class AutonomyTest(Case):
    def test_default_limits(self):
        lim = autonomy.limits(self.store)
        self.assertEqual(lim["max_actions_per_hour"], 20)
        self.assertEqual(lim["max_sends_per_hour"], 5)
        self.assertEqual(lim["max_sends_per_day"], 30)
        self.assertEqual(lim["quiet_hours"], [])
        self.assertTrue(lim["auto_decay_on_silence"])

    def test_send_when_social_low(self):
        out = autonomy.decide(self.store, now=time.time(),
                              state={"core": {"social": 0.2, "energy": 0.7}})
        self.assertEqual(out["action"]["kind"], "send")

    def test_retrieve_when_memory_exists(self):
        write_json(self.store / "sessions" / "autonomy" / "memory.json",
                   {"entries": [{"id": "a", "text": "hello", "ts": 1.0}]})
        out = autonomy.decide(self.store, now=time.time(),
                              state={"core": {"social": 0.9}})
        self.assertEqual(out["action"]["kind"], "retrieve")

    def test_write_when_idle(self):
        out = autonomy.decide(self.store, now=time.time(),
                              state={"core": {"social": 0.9, "energy": 0.8}})
        self.assertEqual(out["action"]["kind"], "write")

    def test_hourly_action_cap_blocks(self):
        now = time.time()
        self.actions([{"ts": now - 5}] * 20)
        out = autonomy.decide(self.store, now=now, state={"core": {"social": 0.2}})
        self.assertIsNone(out["action"])
        self.assertEqual(out["reason"], "limit_actions_hour")

    def test_send_hour_cap_blocks_send(self):
        now = time.time()
        self.actions([{"ts": now - 10, "kind": "send"}] * 5)
        out = autonomy.decide(self.store, now=now, state={"core": {"social": 0.2}})
        self.assertNotEqual((out["action"] or {}).get("kind"), "send")

    def test_quiet_hours_block_send(self):
        self.configure(autonomy={"quiet_hours": ["00:00-23:59"]})
        out = autonomy.decide(self.store, now=time.time(),
                              state={"core": {"social": 0.2}})
        self.assertNotEqual((out["action"] or {}).get("kind"), "send")

    def test_decay_on_silence_throttles(self):
        now = time.time()
        self.actions([{"ts": now - 60}])  # last action 1 min ago
        out = autonomy.decide(self.store, now=now,
                              state={"core": {"social": 0.2}},
                              silence_minutes=24 * 60)
        self.assertIsNone(out["action"])
        self.assertEqual(out["reason"], "decayed_silence")

    def test_core_disabled_no_action(self):
        self.configure(switches={"core_enabled": False})
        out = pipeline.dispatch("autonomy_tick", self.payload())
        self.assertTrue(out["ok"])
        self.assertIsNone(out["action"])
        self.assertEqual(out["reason"], "disabled")

    def test_state_exposes_autonomy_numbers(self):
        self.actions([{"ts": time.time(), "kind": "send"},
                      {"ts": time.time(), "kind": "write"}])
        body = pipeline.dispatch("state", self.payload())["state"]
        self.assertEqual(body["autonomy"]["actions_today"], 2)
        self.assertEqual(body["autonomy"]["sends_today"], 1)
        self.assertEqual(body["autonomy"]["limits"]["max_sends_per_hour"], 5)
        self.assertIn("identity", body)


if __name__ == "__main__":
    unittest.main()
