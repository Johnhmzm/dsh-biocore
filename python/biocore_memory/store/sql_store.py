# -*- coding: utf-8 -*-
"""SQLite store: the structured half of a session (entries, events, claims, journal).

JSON stays the export/compat format; this module is the working store. Opening a
store that only has JSON imports it once, so old installs keep working.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import json
import weakref
import os
import itertools
import sqlite3
import struct
import time
from pathlib import Path

# Batch ids identify one write_doc call. A millisecond stamp alone is not enough:
# two writes inside the same millisecond shared one id, so undo_batch() reverted
# both (a delete from the first write was undone together with the second), which
# corrupted recovery. The stamp stays, a process-local counter makes it unique;
# a restart starting at 1 is fine because the millisecond will have moved on.
# Ids already stored as a bare millisecond remain valid: every read path matches
# the batch string exactly, so old rows keep undoing as before.
_BATCH_SEQ = itertools.count(1)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY,
  -- 业务键：人能读、能引用、能当世界书/提示词里的触发词（借自 shujuku 的 code_index）
  code TEXT,
  -- 逻辑身份：只由内容决定，重排/换库/重新导入都不变
  semantic_id TEXT,
  turn INTEGER CHECK(turn IS NULL OR turn >= 0),
  ts REAL CHECK(ts IS NULL OR ts >= 0),
  role TEXT CHECK(role IS NULL OR role IN ('user', 'assistant', 'system')),
  -- 空条目没有意义：库这一层直接拒，不靠调用方自觉
  text TEXT NOT NULL CHECK(length(text) > 0),
  vector BLOB, hits INTEGER DEFAULT 1, importance REAL DEFAULT 0.2,
  deleted INTEGER DEFAULT 0, raw TEXT
);
CREATE TABLE IF NOT EXISTS units (
  -- 命题单元：一条消息按标点合并成 ~70 字的自足片段，各自一个向量。
  -- 检索用单元（粒度细、匹配准），交付仍用 entries 原文（不失帧）。
  id TEXT PRIMARY KEY,
  entry_id TEXT NOT NULL,
  ord INTEGER,
  text TEXT NOT NULL CHECK(length(text) > 0),
  vector BLOB,
  -- 命题（第二层）：20–35 字的自足陈述，主语必须出现、禁代词。
  -- 老库靠 _ensure_units_schema 的 ALTER TABLE ADD COLUMN 补上，不重建表。
  gist TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_entry ON units(entry_id);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, start INTEGER, end INTEGER, overview TEXT,
  text TEXT, closed_ts REAL, payload TEXT, centroid BLOB
);
CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, subject TEXT, attr TEXT,
  value TEXT, canon TEXT, status TEXT, source TEXT, confidence REAL,
  entity TEXT, thread TEXT, turn INTEGER, event_id TEXT
);
CREATE TABLE IF NOT EXISTS event_state (
  session TEXT PRIMARY KEY, seen INTEGER, global_state TEXT, open_buf TEXT, updated REAL
);
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, batch TEXT, op TEXT, entry_id TEXT, before TEXT, after TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_batch ON changes(batch);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY, v TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_key ON claims(key);
CREATE INDEX IF NOT EXISTS idx_claims_thread ON claims(thread);
CREATE INDEX IF NOT EXISTS idx_journal_kind ON journal(kind);
CREATE INDEX IF NOT EXISTS idx_entries_turn ON entries(turn);
'''


# ---------------------------------------------------------------- 向量编解码
# 全库唯一的向量编解码点：entries / units / events.centroid / 向量回填都走这里。
# 两种精度混存是常态——老行 float32（dim*4 字节），新行 float16（dim*2 字节）。
# 读的时候按 blob 长度 + 库里的 vector_dim 判断，统一交回 float 列表，
# 上层（召回/打分/重排）不需要知道存在两种精度。
DEFAULT_PRECISION = 'float16'
PRECISIONS = ('float32', 'float16')
_PRECISION_ALIASES = {
    'f16': 'float16', 'fp16': 'float16', 'half': 'float16', 'float16': 'float16',
    'f32': 'float32', 'fp32': 'float32', 'single': 'float32', 'float32': 'float32',
}


def normalize_precision(value, default=DEFAULT_PRECISION):
    """收得出来的别名一律收；认不出来退回 default（写库不因为一个词停下）。"""
    text = str(value or '').strip().lower()
    if not text:
        return default
    return _PRECISION_ALIASES.get(text, default)


def pack_vector(vec, precision='float32'):
    """向量 -> BLOB。float32 用 'f'，float16 用 'e'（struct 的半个精度，不需要 numpy）。

    默认仍是 float32：units / events.centroid 这些调用点不传精度，行为与换
    encoding 之前完全一样；entries 的写入点显式传 store 配置的那一档。
    """
    if not vec:
        return None
    try:
        rows = [float(x) for x in vec]
        if not rows:
            return None
        if normalize_precision(precision, 'float32') == 'float16':
            return struct.pack('<%de' % len(rows), *rows)
        return struct.pack('<%df' % len(rows), *rows)
    except Exception:
        return None


def unpack_vector(blob, dim=None):
    """BLOB -> float 列表。dim 给了就按 dim*4 / dim*2 判精度，没给按 4 字节解。

    dim 来自库里的 meta['vector_dim']（每次打包记一次），所以混存读得对；
    老库没有这个键、行也全是 float32，按 4 字节解正好是原来的行为。
    """
    if not blob:
        return []
    size = len(blob)
    try:
        want = int(dim or 0)
    except Exception:
        want = 0
    try:
        if want > 0 and size == want * 2:
            return list(struct.unpack('<%de' % want, blob))
        if want > 0 and size == want * 4:
            return list(struct.unpack('<%df' % want, blob))
        return list(struct.unpack('<%df' % (size // 4), blob))
    except Exception:
        try:                      # 不是 4 的倍数 -> 只可能是半精度
            return list(struct.unpack('<%de' % (size // 2), blob))
        except Exception:
            return []


def _remember_vector_dim(conn, length):
    """记下向量维度（一次就够）：混存读回靠它判 4 字节还是 2 字节。"""
    try:
        dim = int(length or 0)
        if dim > 0:
            conn.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('vector_dim', ?)",
                         (str(dim),))
    except Exception:
        pass


def _vector_dim(conn):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k='vector_dim'").fetchone()
        return int(row[0]) if row and row[0] else None
    except Exception:
        return None


_PRECISION_CACHE = {}


def precision_of(root):
    """store 的向量精度：core_config.json 的 recall.vector_precision（缺省 float16）。

    store 目录下的 core_config.json 找不到就往上一层找（宿主的配置在 <data>/，
    记忆在 <data>/memory/）。按 mtime 缓存：改了配置立刻生效，不用重启。
    """
    if not root:
        return DEFAULT_PRECISION
    base = Path(str(root))
    for path in (base / 'core_config.json', base.parent / 'core_config.json'):
        try:
            info = path.stat()
            # st_mtime is float seconds: two edits inside one second (and a
            # restored backup that keeps its mtime) looked identical. ns + size.
            stamp = (int(info.st_mtime_ns), int(info.st_size))
        except OSError:
            continue
        hit = _PRECISION_CACHE.get(str(path))
        if hit and hit[0] == stamp:
            return hit[1]
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        recall = doc.get('recall') if isinstance(doc, dict) else None
        value = normalize_precision((recall or {}).get('vector_precision')
                                    if isinstance(recall, dict) else None)
        _PRECISION_CACHE[str(path)] = (stamp, value)
        return value
    return DEFAULT_PRECISION


def _pack_entry_vector(conn, vector, precision):
    """entries 的写入：按配置打包，顺手记下维度。"""
    blob = pack_vector(vector, precision)
    if blob:
        _remember_vector_dim(conn, len(vector or []))
    return blob


def db_path(root, session_id):
    return Path(root) / 'sessions' / str(session_id) / 'memory.db'


_OPEN_CONNS = []          # weak refs only: a forgotten handle must not survive
                          # (a strong list would keep the file undeletable forever)


def _ensure_entries_schema(conn):
    """老库补 code 列并回填业务键（M0001 起）。

    约束（CHECK）只对新库生效——SQLite 不能给已有表加 CHECK，
    所以老库靠这条唯一索引 + 这里的一次回填保证不重号。
    """
    try:
        cols = [row[1] for row in conn.execute('PRAGMA table_info(entries)')]
        if 'code' not in cols:
            conn.execute('ALTER TABLE entries ADD COLUMN code TEXT')
        if 'semantic_id' not in cols:
            conn.execute('ALTER TABLE entries ADD COLUMN semantic_id TEXT')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_semantic'
                     ' ON entries(semantic_id) WHERE semantic_id IS NOT NULL')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_code'
                     ' ON entries(code) WHERE code IS NOT NULL')
        missing = conn.execute('SELECT id FROM entries WHERE code IS NULL'
                               ' ORDER BY rowid').fetchall()
        if missing:
            cur = conn.execute('SELECT COALESCE(MAX(CAST(SUBSTR(code, 2) AS INTEGER)), 0)'
                               ' FROM entries WHERE code LIKE \'M%\'').fetchone()
            n = int((cur or [0])[0] or 0)
            for row in missing:
                n += 1
                conn.execute('UPDATE entries SET code=? WHERE id=?',
                             ('M%04d' % n, row[0]))
        conn.commit()
    except Exception:
        pass


def _ensure_units_schema(conn):
    """老库补 gist 列（命题层）。只加列，不重建表、不删数据。

    为什么用 ALTER 而不是重建：units 里可能已经有 16165 行向量（重算代价高），
    ADD COLUMN 是 SQLite 的 O(1) 元数据操作，老行 gist 保持 NULL（= 还没生成），
    回填脚本据此幂等补。
    """
    try:
        cols = [row[1] for row in conn.execute('PRAGMA table_info(units)')]
        if cols and 'gist' not in cols:
            conn.execute('ALTER TABLE units ADD COLUMN gist TEXT')
        conn.commit()
    except Exception:
        pass


def connect(root, session_id):
    """A fresh connection per call; registered so store_close() can release it.

    Pooling was tried and removed: a held handle makes the temporary store
    directory undeletable on Windows (WinError 32). A registry is not pooling --
    it exists because a *forgotten* close is exactly the same bug, and callers
    that legitimately hold a handle (a test, a worker shutdown) need one way to
    let go of all of them.
    """
    path = db_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(SCHEMA)
    conn.commit()
    _ensure_entries_schema(conn)
    _ensure_units_schema(conn)
    try:
        _OPEN_CONNS.append(weakref.ref(conn))
    except TypeError:       # a backend without weakref support: nothing to track
        pass
    return conn


def store_close(path=None):
    """Close every registered connection (or only those of one database).

    Safe to call from anywhere, any number of times: a closed handle is dropped
    from the registry, so the next connect() starts clean.
    """
    root = None
    if path is not None:
        root = str(Path(path).resolve())
    closed = 0
    keep = []
    for ref in list(_OPEN_CONNS):
        conn = ref() if callable(ref) else ref
        if conn is None:
            continue                  # already collected and closed by CPython
        if False:
            pass
        target = ""
        try:
            target = str(Path(conn.execute("PRAGMA database_list").fetchone()[2]))
        except Exception:
            target = ""
        if root is not None and target and Path(target).resolve().as_posix() != Path(root).as_posix():
            keep.append(conn)
            continue
        try:
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
            closed += 1
        except Exception:
            pass
    _OPEN_CONNS[:] = keep
    return closed


@contextlib.contextmanager
def store_open(root, session_id):
    """Hold one store connection for a block and release it on the way out."""
    conn = connect(root, session_id)
    try:
        yield conn
    finally:
        try:
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        _OPEN_CONNS[:] = [ref for ref in _OPEN_CONNS
                          if (ref() if callable(ref) else ref) not in (None, conn)]


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def import_session(root, session_id, force=False, events_path=None):
    """Load sessions/<sid>/memory.json, events/<sid>.json, journal/<sid>.jsonl.

    events_path lets the caller keep its own event-file rule: the pipeline puts
    events under <dataDir>/events even when the store root is <dataDir>/memory.
    """
    root = Path(root)
    conn = connect(root, session_id)
    precision = precision_of(root)
    done = conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
    if done and not force:
        return conn, {'imported': False, 'entries': done}
    memory = _read_json(root / 'sessions' / session_id / 'memory.json', {})
    rows = memory.get('entries') if isinstance(memory, dict) else memory
    count = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        conn.execute(
            'INSERT OR REPLACE INTO entries (id,turn,ts,role,text,vector,hits,importance,deleted,raw)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?)',
            (str(row.get('id') or ''), int(row.get('turn') or 0), float(row.get('ts') or 0.0),
             str(row.get('role') or 'user'), str(row.get('text') or ''),
             _pack_entry_vector(conn, row.get('vector'), precision),
             int(row.get('hits') or 1),
             float(row.get('importance') or 0.2), 1 if row.get('deleted') else 0,
             json.dumps(row, ensure_ascii=False)))
        count += 1
    event_file = Path(events_path) if events_path is not None \
        else root / 'events' / (session_id + '.json')
    events = _read_json(event_file, {})
    for ev in (events.get('events') if isinstance(events, dict) else events) or []:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get('event_id') or '')
        conn.execute(
            'INSERT OR REPLACE INTO events (id,start,end,overview,text,closed_ts,payload)'
            ' VALUES (?,?,?,?,?,?,?)',
            (eid, int(ev.get('start') or 0), int(ev.get('end') or 0),
             str(ev.get('overview') or ''), str(ev.get('text') or ''),
             float(ev.get('closed_ts') or 0.0), json.dumps(ev, ensure_ascii=False)))
        for claim in ev.get('claims') or []:
            if not isinstance(claim, dict):
                continue
            subject = str(claim.get('subject') or '')
            attr = str(claim.get('attr') or '')
            conn.execute(
                'INSERT INTO claims (key,subject,attr,value,canon,status,source,confidence,entity,thread,turn,event_id)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('%s·%s' % (subject, attr), subject, attr, str(claim.get('value') or ''),
                 str(claim.get('canon') or claim.get('value') or ''), str(claim.get('status') or 'current'),
                 str(claim.get('source') or 'unknown'), float(claim.get('confidence') or 0.0),
                 str(claim.get('entity') or ''), str(claim.get('thread') or ''),
                 int(claim.get('turn') or 0), eid))
    try:   # the event rows are the only place these vectors existed
        harvested = harvest_event_vectors(
            (events.get('events') if isinstance(events, dict) else events) or [])
        for entry_id, vector in harvested.items():
            blob = _pack_entry_vector(conn, vector, precision)
            if blob:
                conn.execute('UPDATE entries SET vector=? WHERE id=? AND vector IS NULL',
                             (blob, entry_id))
    except Exception:
        pass
    journal = root / 'journal' / (session_id + '.jsonl')
    if journal.exists():
        for line in journal.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            conn.execute('INSERT INTO journal (ts,kind,payload) VALUES (?,?,?)',
                         (float(row.get('ts') or 0.0), str(row.get('kind') or ''),
                          json.dumps(row, ensure_ascii=False)))
    conn.commit()
    return conn, {'imported': True, 'entries': count}


def export_session(conn, root, session_id):
    """Write the JSON files back so old readers keep working."""
    root = Path(root)
    rows = conn.execute('SELECT raw FROM entries ORDER BY turn').fetchall()
    entries = []
    for (raw,) in rows:
        try:
            entries.append(json.loads(raw))
        except Exception:
            continue
    path = root / 'sessions' / session_id / 'memory.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # atomic export: the JSON is what old readers load; a crash mid-write used
    # to leave a truncated file that json_store then treats as a corrupt store.
    tmp = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
    with open(tmp, 'w', encoding='utf-8') as handle:
        handle.write(json.dumps({'entries': entries}, ensure_ascii=False))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))
    return len(entries)


def harvest_event_vectors(events, open_buf=None):
    """Per-entry vectors carried by event rows: {entry_id: [floats]}.

    In the event-layer mode the only copy of a row's vector lives in the event
    that quotes it, so migrating to sqlite has to lift them into entries.vector
    or the two-stage recall loses its coordinates.
    """
    out = {}
    rows = list(events or []) + list(open_buf or [])
    for ev in rows:
        if not isinstance(ev, dict):
            continue
        for item in ev.get("entries") or []:
            if not isinstance(item, dict):
                continue
            entry_id = str(item.get("id") or "")
            vector = item.get("vector")
            if entry_id and isinstance(vector, (list, tuple)) and vector:
                out[entry_id] = list(vector)
    return out


def update_entry_vectors(root, session_id, vectors):
    """Write per-entry vectors into entries.vector (BLOB); unknown ids are skipped."""
    if not vectors:
        return 0
    root = Path(root)
    conn = connect(root, session_id)
    precision = precision_of(root)
    try:
        written = 0
        for entry_id, vector in vectors.items():
            blob = _pack_entry_vector(conn, vector, precision)
            if not blob:
                continue
            cur = conn.execute('UPDATE entries SET vector=? WHERE id=? AND vector IS NULL',
                               (blob, str(entry_id)))
            written += cur.rowcount or 0
        conn.commit()
        return written
    finally:
        conn.close()


def unit_ids_with_vectors(root, session_id):
    """{entry_id: set(unit_index)} already stored — lets a writer skip re-embedding."""
    root = Path(root)
    conn = connect(root, session_id)
    try:
        out = {}
        try:
            rows = conn.execute(
                'SELECT entry_id, ord FROM units WHERE vector IS NOT NULL').fetchall()
        except Exception:
            return {}
        for entry_id, ordv in rows:
            out.setdefault(str(entry_id), set()).add(int(ordv or 0))
        return out
    finally:
        conn.close()


def save_units(root, session_id, entry_units):
    """Store propositions for an entry: {entry_id: [(ord, text, vector), ...]}.

    Rewriting the same entry replaces its units wholesale so a re-summarised
    message never leaves stale fragments behind.
    """
    if not entry_units:
        return 0
    root = Path(root)
    conn = connect(root, session_id)
    precision = precision_of(root)
    written = 0
    try:
        for entry_id, units in entry_units.items():
            entry_id = str(entry_id or "")
            if not entry_id or not units:
                continue
            # 命题层（gist）是一条条落下来的，重写 units 不能把它丢掉：
            # 单元 id 是确定的 "<entry_id>#<ord>"，先按 id 把已有 gist 抄下来，
            # 重建后同 id 的行原样带上（只增不改的落地方式）。
            kept = {}
            try:
                kept = {str(row[0]): row[1] for row in conn.execute(
                    'SELECT id, gist FROM units WHERE entry_id=?' + ' AND gist IS NOT NULL'
                    ' AND gist != \'\'', (entry_id,))}
            except Exception:
                kept = {}
            conn.execute('DELETE FROM units WHERE entry_id=?', (entry_id,))
            for idx, text, vector in units:
                # 与 entries 同一档精度：units 也是召回用的向量，没有理由多存一倍
                blob = _pack_entry_vector(conn, vector, precision)
                if not blob or not str(text or "").strip():
                    continue
                unit_id = "%s#%d" % (entry_id, int(idx))
                conn.execute(
                    'INSERT OR REPLACE INTO units (id, entry_id, ord, text, vector, gist)'
                    ' VALUES (?,?,?,?,?,?)',
                    (unit_id, entry_id, int(idx), str(text), blob, kept.get(unit_id)))
                written += 1
        conn.commit()
        return written
    finally:
        conn.close()


def gist_stats(root, session_id):
    """{total, with_gist, missing} —— 回填脚本的 dry-run 就报这三个数。"""
    root = Path(root)
    conn = connect(root, session_id)
    try:
        try:
            row = conn.execute(
                "SELECT COUNT(1),"
                " SUM(CASE WHEN gist IS NOT NULL AND gist != '' THEN 1 ELSE 0 END)"
                " FROM units").fetchone()
        except Exception:
            return {'total': 0, 'with_gist': 0, 'missing': 0}
        total = int((row or [0, 0])[0] or 0)
        done = int((row or [0, 0])[1] or 0)
        return {'total': total, 'with_gist': done, 'missing': total - done}
    finally:
        conn.close()


def units_missing_gist(root, session_id, limit=None):
    """[(unit_id, entry_id, ord, text)] —— 还没写命题的单元（按写入顺序）。"""
    root = Path(root)
    conn = connect(root, session_id)
    try:
        sql = ("SELECT id, entry_id, ord, text FROM units"
               " WHERE (gist IS NULL OR gist = '') AND length(text) > 0"
               " ORDER BY entry_id, ord")
        args = ()
        if limit:
            sql += ' LIMIT ?'
            args = (int(limit),)
        try:
            return [tuple(row) for row in conn.execute(sql, args)]
        except Exception:
            return []
    finally:
        conn.close()


def save_gist(root, session_id, unit_id, gist):
    """写一条命题；已有命题的行不改（只增不改 + 幂等）。

    返回 1 = 这次写进去了，0 = 已经有命题（重跑第二次所以必定是 0）。
    """
    text = str(gist or '').strip()
    if not text:
        return 0
    root = Path(root)
    conn = connect(root, session_id)
    try:
        cur = conn.execute(
            "UPDATE units SET gist=? WHERE id=?"
            " AND (gist IS NULL OR gist = '')", (text, str(unit_id)))
        conn.commit()
        return 1 if cur.rowcount > 0 else 0
    finally:
        conn.close()


def load_gists(root, session_id, unit_ids=None):
    """{unit_id: gist}；不给 unit_ids 就返回全部非空命题。"""
    root = Path(root)
    conn = connect(root, session_id)
    try:
        sql = "SELECT id, gist FROM units WHERE gist IS NOT NULL AND gist != ''"
        args = ()
        ids = [str(x) for x in (unit_ids or [])]
        if ids:
            marks = ','.join('?' * len(ids))
            sql += ' AND id IN (%s)' % marks
            args = tuple(ids)
        try:
            return {str(row[0]): str(row[1]) for row in conn.execute(sql, args)}
        except Exception:
            return {}
    finally:
        conn.close()


def load_units(root, session_id):
    """{entry_id: [(ord, text, vector), ...]} — every stored proposition."""
    root = Path(root)
    conn = connect(root, session_id)
    try:
        out = {}
        try:
            rows = conn.execute(
                'SELECT entry_id, ord, text, vector FROM units ORDER BY entry_id, ord'
            ).fetchall()
        except Exception:
            return {}
        dim = _vector_dim(conn)
        for entry_id, ordv, text, blob in rows:
            vector = unpack_vector(blob, dim)
            if not vector:
                continue
            out.setdefault(str(entry_id), []).append(
                (int(ordv or 0), str(text or ""), vector))
        return out
    finally:
        conn.close()


def load_units_for(root, session_id, entry_ids):
    """{entry_id: [(ord, text, vector)]} for the named entries only.

    Recall scores a bounded candidate list, so reading the whole unit table
    would make every turn cost grow with the size of the history. The index
    on units(entry_id) keeps this proportional to the candidate count.
    """
    ids = [str(x) for x in (entry_ids or []) if str(x or "")]
    if not ids:
        return {}
    root = Path(root)
    conn = connect(root, session_id)
    try:
        out = {}
        dim = _vector_dim(conn)
        try:
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    "SELECT entry_id, ord, text, vector FROM units"
                    " WHERE entry_id IN (%s) ORDER BY entry_id, ord" % marks,
                    chunk).fetchall()
                for entry_id, ordv, text, blob in rows:
                    vector = unpack_vector(blob, dim)
                    if not vector:
                        continue
                    out.setdefault(str(entry_id), []).append(
                        (int(ordv or 0), str(text or ""), vector))
        except Exception:
            return {}
        return out
    finally:
        conn.close()


def count_units(root, session_id):
    root = Path(root)
    conn = connect(root, session_id)
    try:
        try:
            row = conn.execute('SELECT COUNT(*) FROM units').fetchone()
        except Exception:
            return 0
        return int(row[0] or 0)
    finally:
        conn.close()


def read_entry_vectors(root, session_id):
    """{entry_id: vector} for every row that has one (the recall coordinates)."""
    root = Path(root)
    conn = connect(root, session_id)
    try:
        out = {}
        dim = _vector_dim(conn)
        for entry_id, blob in conn.execute(
                'SELECT id, vector FROM entries WHERE vector IS NOT NULL').fetchall():
            vector = unpack_vector(blob, dim)
            if vector:
                out[str(entry_id)] = vector
        return out
    finally:
        conn.close()


class WriteNotVerified(RuntimeError):
    """写完回读对不上：库里的内容和要写的不是一回事。"""


def _verify_write(conn, changed, deleted):
    """写后回读校验：条数与文本都要对上，否则抛。

    「宿主保存成功」不等于「数据正确」——上一层的 ok 与这一层的事实是两件事。
    """
    for row in changed or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get('id') or '')
        got = conn.execute('SELECT text, deleted FROM entries WHERE id=?',
                           (rid,)).fetchone()
        want_text = str(row.get('text') or '')
        if row.get('deleted'):
            if not got or not got[1]:
                raise WriteNotVerified('删除标记没有落库: %s' % rid)
            continue
        if not got:
            raise WriteNotVerified('写入后读不到: %s' % rid)
        if str(got[0]) != want_text:
            raise WriteNotVerified('文本不一致: %s' % rid)
    for entry_id in deleted or []:
        got = conn.execute('SELECT 1 FROM entries WHERE id=?',
                           (str(entry_id),)).fetchone()
        if got:
            raise WriteNotVerified('删除没有生效: %s' % entry_id)


def _reinsert_from_raw(conn, entry_id, raw, precision=DEFAULT_PRECISION):
    """按 raw 里的完整字段把一行插回去（只写 id/raw 会撞 NOT NULL）。"""
    try:
        row = json.loads(raw) if raw else {}
    except Exception:
        row = {}
    conn.execute(
        'INSERT OR REPLACE INTO entries'
        ' (id,code,turn,ts,role,text,vector,hits,importance,deleted,raw)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (entry_id, row.get('code'), int(row.get('turn') or 0),
         float(row.get('ts') or 0.0), str(row.get('role') or 'user'),
         str(row.get('text') or '(恢复时缺失)'),
         _pack_entry_vector(conn, row.get('vector'), precision),
         int(row.get('hits') or 1),
         float(row.get('importance') or 0.2), 0, raw))


def changes_tail(root, session_id, limit=50):
    conn = connect(root, session_id)
    try:
        rows = conn.execute('SELECT ts,batch,op,entry_id FROM changes'
                            ' ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()
        return [{'ts': r[0], 'batch': r[1], 'op': r[2], 'entry_id': r[3]} for r in rows]
    finally:
        conn.close()


def undo_batch(root, session_id, batch):
    """把某一批变更撤回去（删除的恢复、写入的还原或删掉）。"""
    conn = connect(root, session_id)
    try:
        rows = conn.execute('SELECT op,entry_id,before,after FROM changes'
                            ' WHERE batch=? ORDER BY id DESC', (str(batch),)).fetchall()
        for op, entry_id, before, after in rows:
            if op == 'delete':
                if before:
                    _reinsert_from_raw(conn, str(entry_id), before, precision_of(root))
            else:
                if before:
                    conn.execute('UPDATE entries SET raw=? WHERE id=?',
                                 (before, str(entry_id)))
                else:
                    conn.execute('DELETE FROM entries WHERE id=?', (str(entry_id),))
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def read_doc(root, session_id, with_vectors=False):
    """The session document as the JSON file used to hold it.

    Insert order is rowid order, which is exactly the order the old file kept,
    so an export round-trips byte for byte.
    """
    root = Path(root)
    conn = connect(root, session_id)
    try:
        meta = {}
        try:
            for key, value in conn.execute('SELECT k, v FROM meta').fetchall():
                meta[str(key)] = value
        except Exception:
            meta = {}
        entries = []
        dim = _vector_dim(conn)   # 混存：entry 的 vector 可能是 f32 也可能是 f16
        # the vector lives in its own BLOB column, not in `raw`: reading only
        # `raw` is why recall saw vector=None on every row
        columns = 'raw, vector, code, semantic_id' if with_vectors else 'raw, code, semantic_id'
        for row in conn.execute('SELECT %s FROM entries ORDER BY rowid' % columns).fetchall():
            raw = row[0]
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except Exception:
                continue
            # 业务键以列为准（老库是迁移回填的，raw 里没有）
            code = row[-2] if len(row) > 1 else None
            if code:
                entry['code'] = str(code)
            sid = row[-1]
            if sid:
                entry['semantic_id'] = str(sid)
            if with_vectors:
                blob = row[1] if len(row) > 1 else None
                entry['vector'] = unpack_vector(blob, dim)
            entries.append(entry)
        try:
            schema_version = int(meta.get('schema_version') or 1)
        except Exception:
            schema_version = 1
        return {'schema_version': schema_version,
                'session_id': str(meta.get('session_id') or session_id),
                'entries': entries}
    finally:
        conn.close()


def read_entry_rows(root, session_id):
    """id -> raw JSON, in insertion order (the diff baseline for incremental writes)."""
    root = Path(root)
    conn = connect(root, session_id)
    try:
        rows = conn.execute('SELECT id, raw FROM entries ORDER BY rowid').fetchall()
        return [(str(rid or ''), raw or '') for rid, raw in rows]
    finally:
        conn.close()


def write_doc(root, session_id, doc, changed=None, deleted=None, stamp_meta=True):
    """Upsert only the entries that changed; never rewrite the whole table.

    changed: iterable of entry dicts (or None to mean "everything in doc")
    deleted: iterable of ids to drop
    """
    root = Path(root)
    conn = connect(root, session_id)
    precision = precision_of(root)
    try:
        if stamp_meta:
            for key, value in (('schema_version', doc.get('schema_version', 1)),
                               ('session_id', doc.get('session_id', session_id))):
                conn.execute('INSERT INTO meta (k, v) VALUES (?, ?)'
                             ' ON CONFLICT(k) DO UPDATE SET v=excluded.v',
                             (key, str(value)))
        batch = '%d-%d' % (int(time.time() * 1000), next(_BATCH_SEQ))
        rows = doc.get('entries') if isinstance(doc.get('entries'), list) else []
        if changed is None:
            changed = rows
        for row in changed:
            if not isinstance(row, dict):
                continue
            _sid = str(row.get('semantic_id') or '')
            if not _sid:
                try:
                    from biocore_memory import semantic_identity as _si
                    _sid = _si.semantic_id(session_id, row.get('ts'),
                                           row.get('role'), row.get('text'))
                except Exception:
                    _sid = ''
            code = str(row.get('code') or '')
            if not code:
                cur = conn.execute('SELECT COALESCE(MAX(CAST(SUBSTR(code, 2) AS INTEGER)), 0)'
                                   ' FROM entries WHERE code LIKE \'M%\'').fetchone()
                code = 'M%04d' % (int((cur or [0])[0] or 0) + 1)
                row['code'] = code
            _prev = conn.execute('SELECT raw FROM entries WHERE id=?',
                                 (str(row.get('id') or ''),)).fetchone()
            conn.execute(
                'INSERT INTO entries (id,code,semantic_id,turn,ts,role,text,vector,hits,importance,deleted,raw)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET'
                ' code=COALESCE(entries.code, excluded.code),'
                ' semantic_id=COALESCE(entries.semantic_id, excluded.semantic_id), turn=excluded.turn,'
                ' ts=excluded.ts, role=excluded.role, text=excluded.text,'
                ' vector=excluded.vector, hits=excluded.hits, importance=excluded.importance,'
                ' deleted=excluded.deleted, raw=excluded.raw',
                (str(row.get('id') or ''), code, _sid, int(row.get('turn') or 0),
                 float(row.get('ts') or 0.0), str(row.get('role') or 'user'),
                 str(row.get('text') or ''),
                 _pack_entry_vector(conn, row.get('vector'), precision),
                 int(row.get('hits') or 1), float(row.get('importance') or 0.2),
                 1 if row.get('deleted') else 0,
                 json.dumps(row, ensure_ascii=False)))
            conn.execute('INSERT INTO changes (ts,batch,op,entry_id,before,after)'
                         ' VALUES (?,?,?,?,?,?)',
                         (time.time(), batch, 'upsert', str(row.get('id') or ''),
                          _prev[0] if _prev else None,
                          json.dumps(row, ensure_ascii=False)))
        for entry_id in deleted or []:
            prev = conn.execute('SELECT raw FROM entries WHERE id=?',
                                (str(entry_id),)).fetchone()
            conn.execute('INSERT INTO changes (ts,batch,op,entry_id,before,after)'
                         ' VALUES (?,?,?,?,?,?)',
                         (time.time(), batch, 'delete', str(entry_id),
                          prev[0] if prev else None, None))
            conn.execute('DELETE FROM entries WHERE id=?', (str(entry_id),))
        conn.commit()
        _verify_write(conn, changed, deleted)
        return True
    finally:
        conn.close()


def export_doc_json(root, session_id, path=None):
    """Write the session document back out as JSON (export/debug only)."""
    doc = read_doc(root, session_id)
    if path is None:
        path = Path(root) / 'sessions' / session_id / 'memory.json'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
    return path


def export_events_json(root, session_id, path=None):
    """The event table as the old events/<sid>.json payload (export/debug only)."""
    state = load_event_state(root, session_id) or {'seen': 0, 'events': [], 'state': {}, 'open': []}
    payload = {'seen': int(state.get('seen') or 0), 'events': state.get('events') or [],
               'state': state.get('state') or {}, 'open': state.get('open') or []}
    if path is None:
        path = Path(root) / 'events' / (session_id + '.json')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
    return path


def claims_by_thread(conn, thread=None, limit=50):
    if thread:
        rows = conn.execute('SELECT key,value,status,confidence,thread,turn FROM claims'
                            ' WHERE thread=? ORDER BY turn DESC LIMIT ?', (thread, limit)).fetchall()
    else:
        rows = conn.execute('SELECT key,value,status,confidence,thread,turn FROM claims'
                            ' ORDER BY turn DESC LIMIT ?', (limit,)).fetchall()
    return [{'key': r[0], 'value': r[1], 'status': r[2], 'confidence': r[3],
             'thread': r[4], 'turn': r[5]} for r in rows]


def journal_tail(conn, limit=50, kind=None):
    if kind:
        rows = conn.execute('SELECT ts,kind,payload FROM journal WHERE kind=?'
                            ' ORDER BY id DESC LIMIT ?', (kind, limit)).fetchall()
    else:
        rows = conn.execute('SELECT ts,kind,payload FROM journal ORDER BY id DESC LIMIT ?',
                            (limit,)).fetchall()
    out = []
    for ts, kind_, payload in rows:
        try:
            out.append(json.loads(payload))
        except Exception:
            out.append({'ts': ts, 'kind': kind_})
    return out


def close_connections(path=None):
    """Alias kept for the test helper: every store handle is released."""
    return store_close(path)


def backfill_vectors(root, session_id, events_path=None):
    """One-time: lift per-entry vectors out of the legacy event file.

    Databases written before the cutover stored event payloads without vectors,
    so a run that finds rows lacking coordinates repairs them from the JSON the
    events were originally built from. Idempotent: a fully populated table is a
    single COUNT.
    """
    root = Path(root)
    conn = connect(root, session_id)
    try:
        done = conn.execute(
            "SELECT v FROM meta WHERE k='vector_backfill'").fetchone()
        if done is not None:
            return 0          # one-shot: new rows get their vector from the save path
        missing = conn.execute(
            'SELECT COUNT(*) FROM entries WHERE vector IS NULL').fetchone()[0]
        if not missing:
            conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('vector_backfill','1')")
            conn.commit()
            return 0
        event_file = Path(events_path) if events_path is not None \
            else root / 'events' / (session_id + '.json')
        legacy = _read_json(event_file, {})
        rows = legacy.get('events') if isinstance(legacy, dict) else legacy
        vectors = harvest_event_vectors(rows or [])
        written = 0
        precision = precision_of(root)
        for entry_id, vector in vectors.items():
            blob = _pack_entry_vector(conn, vector, precision)
            if not blob:
                continue
            cur = conn.execute(
                'UPDATE entries SET vector=? WHERE id=? AND vector IS NULL',
                (blob, str(entry_id)))
            written += cur.rowcount or 0
        conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('vector_backfill','1')")
        conn.commit()
        return written
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def open_or_import(root, session_id, events_path=None):
    """Never break an old install: import JSON when the db is missing."""
    root = Path(root)
    fresh = not db_path(root, session_id).exists()
    conn, info = import_session(root, session_id, force=False, events_path=events_path)
    # the caller owns the connection (legacy contract): never close it here
    info['vectors'] = backfill_vectors(root, session_id, events_path=events_path)
    info['fresh'] = fresh
    return conn, info


atexit.register(store_close)


def _ensure_events_centroid(conn):
    """Old databases predate the centroid column; add it in place."""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "centroid" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN centroid BLOB")
    except Exception:
        pass


# 事件 centroid 用的是同一个 pack_vector / unpack_vector（不传精度 = float32），
# 一行 4 KB 而不是 ~20 KB 的 JSON 文本。这里曾经有一份重复定义，会盖掉上面的
# 那一份，导致"改编解码点"看起来改了却没生效，已删。


def _ensure_events_span(conn):
    """老库补 started_ts / ended_ts 两列（事件的时间跨度）。只加列，不重建表。

    为什么不让模型猜：起止时间是成员 ts 的 min/max，机械可得；模型只给
    overview/claims。两列都可空：算不出来（成员没有 ts）就留 NULL，绝不填 0。
    """
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "started_ts" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN started_ts REAL")
        if "ended_ts" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN ended_ts REAL")
        conn.commit()
    except Exception:
        pass


def event_span(entries):
    """成员 ts 的 (min, max)；拿不到就给 (None, None)。"""
    stamps = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get('ts') or 0.0)
        except Exception:
            continue
        if value > 0:
            stamps.append(value)
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def plan_event_spans(db_path):
    """盘点一个库：多少行能算、多少行已经有了、多少行算不出来。只读。"""
    import sqlite3 as _sq
    from pathlib import Path as _Path
    path = _Path(str(db_path))
    out = {'rows': 0, 'fillable': 0, 'already': 0, 'uncomputable': 0, 'plan': []}
    if not path.exists():
        return out
    conn = _sq.connect(str(path))
    try:
        _ensure_events_span(conn)
        try:
            rows = conn.execute('SELECT id, payload, started_ts, ended_ts'
                                ' FROM events').fetchall()
        except Exception:
            return out
        for eid, payload, started, ended in rows:
            out['rows'] += 1
            if started is not None and ended is not None:
                out['already'] += 1
                continue
            try:
                doc = json.loads(payload) if payload else {}
            except Exception:
                doc = {}
            first, last = event_span((doc or {}).get('entries'))
            if first is None:
                out['uncomputable'] += 1
                continue
            out['fillable'] += 1
            out['plan'].append((str(eid), float(first), float(last)))
        return out
    finally:
        conn.close()


def apply_event_spans(db_path, plan=None):
    """把盘点结果写进去：只填 NULL 的行，已有的一个字节都不改（幂等）。"""
    import sqlite3 as _sq
    from pathlib import Path as _Path
    path = _Path(str(db_path))
    if not path.exists():
        return 0
    items = plan
    if items is None:
        items = plan_event_spans(db_path)['plan']
    if not items:
        return 0
    conn = _sq.connect(str(path))
    written = 0
    try:
        _ensure_events_span(conn)
        for eid, first, last in items:
            cur = conn.execute(
                'UPDATE events SET started_ts=?, ended_ts=?'
                ' WHERE id=? AND (started_ts IS NULL OR ended_ts IS NULL)',
                (float(first), float(last), str(eid)))
            written += 1 if cur.rowcount > 0 else 0
        conn.commit()
    finally:
        conn.close()
    return written


def _event_payload(ev):
    """The event row minus the per-entry vectors, which hydrate from the cache."""
    out = dict(ev or {})
    rows = []
    for item in ev.get("entries") or []:
        if not isinstance(item, dict):
            continue
        rows.append({key: item.get(key) for key in
                     ("id", "turn", "ts", "role", "text")})
    out["entries"] = rows
    out.pop("vector", None)
    return out


def save_event_state(root, session_id, seen, events, global_state, open_buf):
    """Incrementally upsert the session's events; no whole-table rewrite."""
    conn = connect(root, session_id)
    try:
        _ensure_events_centroid(conn)
        _ensure_events_span(conn)
        live = {str(ev.get("event_id") or "") for ev in (events or [])
                if isinstance(ev, dict)}
        # cheap fingerprint of what is already on disk: one small SELECT, then
        # only the rows that actually changed are written (a closed event never
        # changes again, so a turn that closes one event writes one row)
        existing = {}
        try:
            for row in conn.execute(
                    "SELECT id, start, end, overview, length(text), length(payload)"
                    " FROM events").fetchall():
                existing[str(row[0])] = (int(row[1] or 0), int(row[2] or 0),
                                         str(row[3] or ""), int(row[4] or 0), int(row[5] or 0))
        except Exception:
            existing = {}
        for (eid,) in conn.execute("SELECT id FROM events").fetchall():
            if str(eid) not in live:
                conn.execute("DELETE FROM events WHERE id=?", (eid,))
        for ev in events or []:
            if not isinstance(ev, dict):
                continue
            _payload = json.dumps(_event_payload(ev), ensure_ascii=False)
            key = (str(ev.get("event_id") or ""), int(ev.get("start") or 0),
                   int(ev.get("end") or 0), str(ev.get("overview") or ""),
                   len(str(ev.get("text") or "")), len(_payload))
            if existing.get(key[0]) == key[1:]:
                continue
            _first, _last = event_span(ev.get("entries"))
            conn.execute(
                "INSERT INTO events (id,start,end,overview,text,closed_ts,payload,"
                " centroid,started_ts,ended_ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
                " start=excluded.start, end=excluded.end, overview=excluded.overview,"
                " text=excluded.text, closed_ts=excluded.closed_ts,"
                " payload=excluded.payload, centroid=excluded.centroid,"
                " started_ts=COALESCE(events.started_ts, excluded.started_ts),"
                " ended_ts=COALESCE(events.ended_ts, excluded.ended_ts)",
                (str(ev.get("event_id") or ""), int(ev.get("start") or 0),
                 int(ev.get("end") or 0), str(ev.get("overview") or ""),
                 str(ev.get("text") or ""), float(ev.get("closed_ts") or 0.0),
                 _payload,
                 pack_vector(ev.get("vector")), _first, _last))
        conn.execute(
            "INSERT INTO event_state (session,seen,global_state,open_buf,updated)"
            " VALUES (?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET"
            " seen=excluded.seen, global_state=excluded.global_state,"
            " open_buf=excluded.open_buf, updated=excluded.updated",
            (session_id, int(seen or 0), json.dumps(global_state or {}, ensure_ascii=False),
             json.dumps(open_buf or [], ensure_ascii=False), time.time()))
        conn.commit()
        return True
    finally:
        conn.close()


def load_event_state(root, session_id):
    """Read back what save_event_state wrote; None when the session is new."""
    import os as _os
    if not db_path(root, session_id).exists() and not _os.environ.get("BIOCORE_SQL_FIRST"):
        return None
    conn = connect(root, session_id)
    try:
        row = conn.execute("SELECT seen, global_state, open_buf FROM event_state WHERE session=?",
                           (session_id,)).fetchone()
        events = []
        try:
            rows = conn.execute(
                "SELECT payload, centroid FROM events ORDER BY start").fetchall()
        except Exception:
            rows = [(p, None) for (p,) in conn.execute(
                "SELECT payload FROM events ORDER BY start").fetchall()]
        for payload, centroid in rows:
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            if centroid:
                ev["vector"] = unpack_vector(centroid)
            events.append(ev)
        if row is None and not events:
            return None
        seen = int((row or [0])[0] or 0)
        try:
            state = json.loads((row or [0, "{}"])[1] or "{}")
        except Exception:
            state = {}
        try:
            open_buf = json.loads((row or [0, "{}", "[]"])[2] or "[]")
        except Exception:
            open_buf = []
        return {"seen": seen, "events": events, "state": state, "open": open_buf}
    finally:
        conn.close()

def update_trace_fields(store, session_id, updates, *, use_json=None):
    """只写使用记录（hits / last_used），纯 SQL，不做 json.loads / json.dumps。

    为什么是 SQL 而不是“读整页→改→整页写回”：整页重写必然带进顺手整理，
    vector: null 被写成 [] 就是这么来的（2026-11-13 的 bug）。这里只碰指定列与
    指定 JSON 路径，其它字节由 sqlite 保留。

    使用记录是“谁用过”的依据、不是记忆内容：禁写会让「忘」失去现实依据，
    延后写会在崩溃时丢计数。

    updates = {entry_id: {"hits": int, "last_used": float}}
    返回写入的行数；失败抛异常由调用方决定（交付路径应当 try/except + warn）。
    """
    updates = {str(k): v for k, v in (updates or {}).items() if k}
    if not updates:
        # 空批：不开事务、不产生任何 SQL，只留一行 debug
        logging.getLogger(__name__).debug(
            '[trace] nothing to update (session=%s)', session_id)
        return 0
    path = Path(str(store)) / 'sessions' / str(session_id) / 'memory.db'
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        if use_json is None:
            try:
                use_json = conn.execute("SELECT json_valid('{}')").fetchone()[0] == 1
            except Exception:
                use_json = False
        written = 0
        conn.execute('BEGIN')
        try:
            for entry_id, fields in updates.items():
                hits = fields.get('hits')
                last_used = fields.get('last_used')
                if use_json:
                    cur = conn.execute(
                        "UPDATE entries SET hits = ?, "
                        "raw = json_set(raw, '$.hits', ?, '$.last_used', ?) "
                        "WHERE id = ?", (hits, hits, last_used, entry_id))
                else:
                    # 降级：只更新列，raw 一个字节都不动
                    cur = conn.execute(
                        'UPDATE entries SET hits = ? WHERE id = ?', (hits, entry_id))
                written += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return written
    finally:
        conn.close()
