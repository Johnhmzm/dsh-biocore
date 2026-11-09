"""Incremental vector sidecar (SQLite BLOB), modeled after the livingmemory
plugin's separation of document storage from the FAISS index.

Keeping 1024-d vectors out of the JSON metadata file avoids rewriting the whole
memory file on every turn. Vectors are written only when they are created or
updated. Connections are short-lived so temp dirs can be cleaned up on Windows.
"""
from __future__ import annotations

import sqlite3
import struct
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


class SQLiteVectorStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self._conn() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS vectors ("
                "id TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL, updated REAL NOT NULL)")

    def _conn(self):
        con = sqlite3.connect(str(self.path), timeout=10.0)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def put(self, key: str, vec: Sequence[float]) -> None:
        vals = [float(x) for x in vec]
        if not vals:
            return
        blob = struct.pack("%df" % len(vals), *vals)
        with self.lock:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO vectors(id, dim, vec, updated) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET dim=excluded.dim, vec=excluded.vec, updated=excluded.updated",
                    (str(key), len(vals), sqlite3.Binary(blob), time.time()))

    def get_many(self, keys: Iterable[str]) -> Dict[str, List[float]]:
        keys = [str(k) for k in keys]
        out: Dict[str, List[float]] = {}
        if not keys:
            return out
        with self.lock:
            with self._conn() as con:
                for i in range(0, len(keys), 500):
                    chunk = keys[i:i + 500]
                    q = ",".join("?" for _ in chunk)
                    rows = con.execute("SELECT id, dim, vec FROM vectors WHERE id IN (%s)" % q, chunk).fetchall()
                    for key, dim, blob in rows:
                        out[str(key)] = list(struct.unpack("%df" % int(dim), bytes(blob)))
        return out

    def prune(self, active_keys: Iterable[str]) -> int:
        active = {str(k) for k in active_keys}
        with self.lock:
            with self._conn() as con:
                rows = con.execute("SELECT id FROM vectors").fetchall()
                stale = [str(r[0]) for r in rows if str(r[0]) not in active]
                for i in range(0, len(stale), 500):
                    chunk = stale[i:i + 500]
                    q = ",".join("?" for _ in chunk)
                    con.execute("DELETE FROM vectors WHERE id IN (%s)" % q, chunk)
                return len(stale)

    def count(self) -> int:
        with self.lock:
            with self._conn() as con:
                return int(con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])

    def close(self) -> None:
        return None
