# -*- coding: utf-8 -*-
"""task-221 (batch 2): query recall injection + embedding stats persistence."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from biocore_memory.recall import embedding
from biocore_runtime import pipeline


def entry(i, text, ts, importance=0.5):
    return {"id": i, "text": text, "role": "user", "ts": ts,
            "importance": importance, "hits": 1, "scope": "default",
            "memory_confidence": 0.7, "memory_source": "direct"}


class InjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "data" / "memory"
        self.entries = [
            entry("new", "今天买了咖啡", 1700000300.0),
            entry("old", "上次说的苹果很甜", 1700000100.0),
            entry("mid", "明天要去公园", 1700000200.0),
        ]
        (self.store / "sessions" / "s1").mkdir(parents=True)
        (self.store / "sessions" / "s1" / "memory.json").write_text(
            json.dumps({"entries": self.entries}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def inject(self, **kwargs):
        return pipeline.inject_block("s1", str(self.store), **kwargs)


class QueryModeTest(InjectCase):
    def test_query_recall_orders_by_query(self):
        out = self.inject(query="苹果", mode="query", top_k=3)
        self.assertEqual(out["ids"][0], "old")

    def test_recent_mode_keeps_newest_first(self):
        out = self.inject(query="苹果", mode="recent", top_k=3)
        self.assertEqual(out["ids"][0], "new")

    def test_default_mode_is_query(self):
        out = self.inject(query="苹果", top_k=2)
        self.assertEqual(out["ids"][0], "old")
        self.assertEqual(len(out["ids"]), 2)

    def test_empty_query_falls_back_recent(self):
        out = self.inject(query="", mode="query", top_k=3)
        self.assertEqual(out["ids"][0], "new")

    def test_engine_failure_falls_back_recent(self):
        saved = pipeline._inject_engine
        pipeline._inject_engine = lambda store, entries: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            out = self.inject(query="苹果", mode="query", top_k=2)
        finally:
            pipeline._inject_engine = saved
        self.assertEqual(out["ids"][0], "new")
        self.assertEqual(len(out["ids"]), 2)

    def test_output_format_and_budget(self):
        out = self.inject(query="苹果", mode="query", top_k=3, max_chars=60)
        self.assertTrue(out["text"].startswith("你记得这些事："))
        self.assertTrue(all(line.startswith("- ") for line in out["text"].split("\n")[1:]))
        self.assertLessEqual(out["chars"], 60)
        self.assertEqual(out["chars"], len(out["text"]))
        self.assertTrue(set(out["ids"]).issubset({"new", "old", "mid"}))


class RerankTest(InjectCase):
    def test_rerank_reorders_when_enabled(self):
        class FakeRerank:
            def __init__(self):
                self.calls = 0

            def rerank_documents(self, query, documents, config):
                self.calls += 1
                return [{"index": 2}, {"index": 0}, {"index": 1}]

        fake = FakeRerank()
        saved = pipeline._rerank_mod
        pipeline._rerank_mod = fake
        try:
            out = self.inject(query="苹果", mode="query", top_k=8,
                              rerank_config={"rerank_enabled": True})
        finally:
            pipeline._rerank_mod = saved
        self.assertEqual(fake.calls, 1)
        # engine put 苹果 (old) first; rerank's order must win instead
        self.assertEqual(out["ids"], ["mid", "old", "new"])

    def test_rerank_off_does_not_call(self):
        class Exploding:
            def rerank_documents(self, *args, **kwargs):
                raise AssertionError("rerank must not run")

        saved = pipeline._rerank_mod
        pipeline._rerank_mod = Exploding()
        try:
            out = self.inject(query="苹果", mode="query", top_k=8,
                              rerank_config={"rerank_enabled": False})
        finally:
            pipeline._rerank_mod = saved
        self.assertEqual(out["ids"][0], "old")


class EmbeddingFallbackTest(InjectCase):
    def test_embedding_provider_failure_degrades_to_lexical(self):
        class BadProvider:
            def embed_text(self, text):
                raise RuntimeError("no network")

        from biocore_memory.engine import MemoryEngine
        from biocore_memory.store.json_store import MemoryStore

        def engine_with_bad_embedder(store, entries):
            memory = MemoryStore(doc={"entries": [dict(item) for item in entries]})
            return MemoryEngine(store=memory, embedder=BadProvider(),
                                config={"flags": {}})

        saved = pipeline._inject_engine
        pipeline._inject_engine = engine_with_bad_embedder
        try:
            out = self.inject(query="苹果", mode="query", top_k=3)
        finally:
            pipeline._inject_engine = saved
        self.assertEqual(out["ids"][0], "old")

    def test_learning_flags_default_on(self):
        captured = {}

        class CaptureEngine:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def recall(self, query, budget=5):
                return []

        saved = pipeline._inject_engine
        saved_engine = None
        try:
            from biocore_memory.engine import MemoryEngine
            saved_engine = MemoryEngine
            import biocore_memory.engine as engine_module
            engine_module.MemoryEngine = CaptureEngine
            try:
                pipeline.inject_block("s1", str(self.store), query="苹果")
            finally:
                engine_module.MemoryEngine = saved_engine
        finally:
            pipeline._inject_engine = saved
        flags = (captured.get("config") or {}).get("flags") or {}
        for key in ("mood_recall", "affect_encoding",
                    "memory_feedback", "preference_drift"):
            self.assertIs(flags.get(key), True)


class EmbedStatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.stats_file = self.dir / "embed_stats.json"
        self.cache_dir = self.dir / "cache"
        self._env = {k: os.environ.get(k) for k in
                     ("BIOCORE_EMBED_STATS_FILE", "BIOCORE_EMBEDDING_CACHE_FILE")}
        os.environ["BIOCORE_EMBED_STATS_FILE"] = str(self.stats_file)
        os.environ["BIOCORE_EMBEDDING_CACHE_FILE"] = str(self.cache_dir)
        embedding.embed_stats(reset=True)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_miss_remote_then_disk_hit(self):
        class Provider:
            def embed_text(self, text):
                return [0.1] * 8

        self.assertIsNotNone(embedding.embed("hello world", Provider()))
        first = json.loads(self.stats_file.read_text(encoding="utf-8"))
        self.assertEqual(first["miss"], 1)
        self.assertEqual(first["remote"], 1)
        self.assertEqual(first["disk_hit"], 0)
        embedding.embed("hello world", Provider())
        second = json.loads(self.stats_file.read_text(encoding="utf-8"))
        self.assertEqual(second["disk_hit"], 1)

    def test_stats_action_reads_persisted_file(self):
        self.stats_file.write_text(json.dumps(
            {"hit": 2, "disk_hit": 3, "miss": 4, "remote": 5,
             "local": 6, "fail": 7}), encoding="utf-8")
        store = self.dir / "memory"
        out = pipeline.dispatch("stats", {"store": str(store)})
        self.assertEqual(out["embed"]["disk_hit"], 3)
        self.assertEqual(out["embed"]["miss"], 4)
        self.assertEqual(out["embed"]["remote"], 5)


if __name__ == "__main__":
    unittest.main()
