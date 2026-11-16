# -*- coding: utf-8 -*-
"""JSON store: backward-compatible load, atomic save, corrupt backup.

Writes are debounced: mutations only touch memory and mark the store dirty;
the file is rewritten when 2 s have passed since the last save, or after 20
accumulated changes, or on an explicit flush()/close()/process exit.
"""
from __future__ import annotations
import atexit, itertools, json, os, shutil, time, weakref
from pathlib import Path

from ..core.config import SCHEMA_VERSION
from ..core.util import normalize_text
from .versions import record_version
from .tombstone import append_tombstone

DEBOUNCE_SECONDS = 2.0
DEBOUNCE_CHANGES = 20

_LIVE = weakref.WeakSet()


def _flush_all():
    for store in list(_LIVE):
        try:
            store.flush()
        except Exception:
            pass


atexit.register(_flush_all)


class MemoryStore:
    def __init__(self, path=None, doc=None):
        self.path = Path(path) if path else None
        self.doc = {"entries": [], "tombstones": []}
        self._dirty = 0
        self._saves = 0
        self._tmp_seq = itertools.count(1)
        self._last_save = time.monotonic()
        _LIVE.add(self)
        if isinstance(doc, dict):
            self.import_doc(doc)

    def import_doc(self, doc):
        data = doc if isinstance(doc, dict) else {}
        out = dict(data)
        entries = out.get("entries")
        out["entries"] = [dict(e) for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
        tombs = out.get("tombstones")
        out["tombstones"] = [dict(t) for t in tombs if isinstance(t, dict)] if isinstance(tombs, list) else []
        out.setdefault("schema_version", SCHEMA_VERSION)
        meta = out.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        stats = meta.get("stats")
        meta["stats"] = stats if isinstance(stats, dict) else {}
        out["meta"] = meta
        self.doc = out
        self._touch()
        return self

    @classmethod
    def load(cls, path):
        p = Path(path)
        store = cls(path=p)
        if not p.exists():
            return store
        try:
            store.import_doc(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            try:
                # one backup per corruption event: a second corrupt copy used to
                # be dropped because the first .corrupt.bak already existed.
                stamp = time.strftime("%Y%m%d-%H%M%S")
                bak = p.with_name("%s.corrupt-%s.bak" % (p.name, stamp))
                if not bak.exists():
                    shutil.copy2(p, bak)
                legacy = p.with_suffix(p.suffix + ".corrupt.bak")
                if not legacy.exists():
                    shutil.copy2(p, legacy)
            except Exception:
                pass
        return store

    def save(self, path=None):
        target = Path(path) if path else self.path
        if target is None:
            # 只读视图（pipeline 的 _inject_engine 用 doc 造的就是它）：没有位置可写，
            # 不落盘也不算失败。以前这里抛 ValueError，召回在"需要写回一次"的库上
            # 直接炸掉（实测：store has no path）。
            self._dirty = 0
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        # A fixed "<file>.tmp" let two writers (host + dsh_cli) truncate each
        # other's temp file and then replace with half a document. The temp
        # name now carries pid + a per-store counter, and the bytes are flushed
        # to the OS before the rename, so a reader never sees a partial file.
        tmp = target.with_name("%s.%d.%d.tmp" % (target.name, os.getpid(),
                                                 next(self._tmp_seq)))
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(self.doc, ensure_ascii=False, indent=1))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp), str(target))
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
        self._dirty = 0
        self._last_save = time.monotonic()
        self._saves += 1
        return target

    # -- debounced persistence ---------------------------------------------
    def _touch(self):
        self._dirty += 1
        return self._maybe_save()

    def maybe_save(self):
        """Debounced persistence: write only when due (public wrapper).

        The engine used to call save() unconditionally after every recall, which
        defeated the 2 s / 20-change debounce documented at the top of this
        module (one full rewrite of the store per read). Callers that must be
        durable (explicit save(), CLI exit) still call save()/flush().
        """
        return self._maybe_save()

    def _maybe_save(self):
        if not self._dirty:
            return False
        if self._dirty >= DEBOUNCE_CHANGES:
            return self.flush()
        if (time.monotonic() - self._last_save) >= DEBOUNCE_SECONDS:
            return self.flush()
        return False

    def flush(self):
        """Write now if anything is dirty; returns whether a save happened."""
        if not self._dirty:
            return False
        try:
            self.save()
        except Exception:
            self._dirty = max(1, self._dirty)      # keep it dirty, caller sees the error
            raise
        return True

    def close(self):
        return self.flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.flush()
        except Exception:
            pass
        return False

    def __del__(self):
        try:
            if getattr(self, "_dirty", 0):
                self.flush()
        except Exception:
            pass

    @property
    def entries(self):
        return self.doc.setdefault("entries", [])

    @property
    def tombstones(self):
        return self.doc.setdefault("tombstones", [])

    def find(self, entry_id):
        for e in self.entries:
            if str(e.get("id") or "") == str(entry_id):
                return e
        return None

    def add(self, entry):
        if not isinstance(entry, dict):
            return None
        item = dict(entry)
        text = normalize_text(item.get("text"))
        if len(text) < 2:
            return None
        item["text"] = text
        item.setdefault("id", "")
        item.setdefault("role", "user")
        item.setdefault("ts", 0.0)
        item.setdefault("hits", 1)
        item.setdefault("importance", 0.5)
        item.setdefault("pin", False)
        item.setdefault("previous_versions", [])
        item["schema_version"] = SCHEMA_VERSION
        self.entries.append(item)
        self._touch()
        return item

    def update(self, entry_id, **fields):
        entry = self.find(entry_id)
        if entry is None:
            return None
        record_version(entry)
        for key, value in fields.items():
            entry[key] = value
        self._touch()
        return entry

    def remove(self, entry_id, now=0.0, reason="deleted", hard=False):
        entry = self.find(entry_id)
        if entry is None:
            return False
        self.entries.remove(entry)
        if not hard:
            append_tombstone(self.tombstones, entry, now=now, reason=reason)
        self._touch()
        return True

    def pin(self, entry_id, pinned=True):
        entry = self.find(entry_id)
        if entry is None:
            return False
        entry["pin"] = bool(pinned)
        self._touch()
        return True

    # -- persisted read-only monitoring counters ---------------------------
    @property
    def meta(self):
        meta = self.doc.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            self.doc["meta"] = meta
        stats = meta.get("stats")
        if not isinstance(stats, dict):
            meta["stats"] = {}
        return meta

    def stats(self):
        raw = self.meta.get("stats") or {}
        out = {}
        for key, value in raw.items():
            try:
                out[str(key)] = int(value)
            except Exception:
                out[str(key)] = 0
        return out

    def bump_stats(self, key, amount=1):
        stats = self.meta.setdefault("stats", {})
        try:
            stats[str(key)] = int(stats.get(str(key), 0) or 0) + int(amount or 1)
        except Exception:
            stats[str(key)] = int(amount or 1)
        self._touch()
        return stats[str(key)]

    def export(self):
        return json.loads(json.dumps(self.doc, ensure_ascii=False))
