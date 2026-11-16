# -*- coding: utf-8 -*-
"""第三层的两张索引表：day_events（哪一天有哪几件事件）与 event_chain（事件链）。

为什么不是"给 events 加一列 day"：
  - 事件是既成的（定型不改），它落在哪一天可以**有多个归属**（跨零点、被回忆命中并入）；
  - 链是 append-only 的先后关系，"第几条接在哪条后面"用自增 seq 表达，改不了也用不着改。

只增不改：
  - 两张表都 CREATE TABLE IF NOT EXISTS，不 DROP、不 ALTER 旧表；
  - 写入 INSERT OR IGNORE（唯一约束挡重复），同一事件重复登记 = 0 写入；
  - **同一事件的两处登记在一个事务里**：day_events 与 event_chain 要么都进、要么都不进。
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DAY_TABLE = 'day_events'
CHAIN_TABLE = 'event_chain'
# 切天用北京时间 00:00（设计指定）；写成常量，别散在调用点上
DAY_TZ = timezone(timedelta(hours=8))

DDL = (
    'CREATE TABLE IF NOT EXISTS day_events ('
    ' day_key TEXT NOT NULL,'
    ' event_id TEXT NOT NULL,'
    ' ordinal INTEGER,'
    ' added_at REAL,'
    ' UNIQUE(day_key, event_id))',
    'CREATE INDEX IF NOT EXISTS idx_day_events_day'
    ' ON day_events(day_key, ordinal)',
    'CREATE TABLE IF NOT EXISTS event_chain ('
    ' seq INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' event_id TEXT NOT NULL,'
    ' prev_seq INTEGER,'
    ' added_at REAL,'
    ' reason TEXT,'
    ' UNIQUE(event_id))',
)

# 默认建边的理由（同天 / 跨天顺序）；模型另写理由时覆盖它，但边的行只增不改
REASON_SAME_DAY = '同一天'
REASON_SEQUENCE = '顺序'


def _ensure_reason(conn):
    """老库补 reason 列（第 2 步建的表没有它）。只加列，不动行。"""
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(event_chain)')}
        if cols and 'reason' not in cols:
            conn.execute('ALTER TABLE event_chain ADD COLUMN reason TEXT')
            conn.commit()
    except Exception:
        pass


def day_key_of(ts, tz=DAY_TZ):
    """时间戳 -> 北京时间的 'YYYY-MM-DD'；拿不到时间返回 ''（调用方据此跳过）。"""
    try:
        stamp = float(ts)
    except Exception:
        return ''
    if stamp <= 0:
        return ''
    return datetime.fromtimestamp(stamp, tz).strftime('%Y-%m-%d')


def _connect(db_path):
    # see M-28: WAL + busy_timeout, or BEGIN IMMEDIATE below fails under load
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    for ddl in DDL:
        conn.execute(ddl)
    _ensure_reason(conn)
    conn.commit()
    return conn


def next_ordinal(conn, day_key):
    """这一天已经登记了几件 -> 下一件的序号（0 基）。"""
    try:
        row = conn.execute(
            'SELECT MAX(ordinal) FROM day_events WHERE day_key=?',
            (str(day_key or ''),)).fetchone()
        # 空日 MAX() 是 NULL，旧写法 (None or 0)+1 让序号从 1 开始（docstring 说 0 基），
        # 而"当天已有行但 ordinal 全是 NULL"时每次调用都返回 1，序号会重复。
        highest = row[0] if row else None
        return (int(highest) if highest is not None else -1) + 1
    except Exception:
        return 0


def last_seq(conn):
    try:
        row = conn.execute('SELECT COALESCE(MAX(seq), 0) FROM event_chain').fetchone()
        return int((row or [0])[0] or 0)
    except Exception:
        return 0


def _default_reason(conn, day_key, prev_seq):
    """默认建边理由：上一条边落在同一天就是「同一天」，否则是「顺序」。"""
    if not prev_seq:
        return REASON_SEQUENCE
    try:
        row = conn.execute(
            'SELECT c.event_id FROM event_chain c WHERE c.seq=?', (int(prev_seq),)).fetchone()
        if not row:
            return REASON_SEQUENCE
        prev_id = str(row[0])
        hit = conn.execute(
            'SELECT 1 FROM day_events WHERE event_id=? AND day_key=?',
            (prev_id, str(day_key or ''))).fetchone()
        return REASON_SAME_DAY if hit else REASON_SEQUENCE
    except Exception:
        return REASON_SEQUENCE


def record(db_path, event_id, day_key, ordinal=None, prev_seq=None, now=None,
           reason=None):
    """同一事件写两处：day_events + event_chain，一个事务。

    返回 {'day': 0/1, 'chain': 0/1, 'ordinal': n, 'seq': n, 'reason': ..}；
    两处都已存在（重复登记）时 day=0、chain=0，且一条都不写。
    reason 不给就按默认规则（上一条边在同一天 -> 「同一天」，否则「顺序」）。
    """
    eid = str(event_id or '').strip()
    key = str(day_key or '').strip()
    if not eid or not key:
        return {'day': 0, 'chain': 0, 'ordinal': None, 'seq': None,
                'reason': 'missing_id_or_day'}
    stamp = float(now if now is not None else time.time())
    conn = _connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        place = next_ordinal(conn, key) if ordinal is None else int(ordinal)
        link = last_seq(conn) if prev_seq is None else int(prev_seq)
        # 理由必须在插入前算：插入后 last_seq 就变成这一条了
        why = str(reason) if reason else _default_reason(conn, key, link)
        try:
            cur_day = conn.execute(
                'INSERT OR IGNORE INTO day_events (day_key, event_id, ordinal, added_at)'
                ' VALUES (?,?,?,?)', (key, eid, place, stamp))
            cur_chain = conn.execute(
                'INSERT OR IGNORE INTO event_chain (event_id, prev_seq, added_at, reason)'
                ' VALUES (?,?,?,?)', (eid, link, stamp, why))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        seq = None
        row = conn.execute('SELECT seq, ordinal FROM event_chain c'
                           ' JOIN day_events d ON d.event_id = c.event_id'
                           ' WHERE c.event_id=?', (eid,)).fetchone()
        if row:
            seq = int(row[0])
        return {'day': 1 if cur_day.rowcount > 0 else 0,
                'chain': 1 if cur_chain.rowcount > 0 else 0,
                'ordinal': place, 'seq': seq, 'reason': why}
    finally:
        conn.close()


def events_of_day(db_path, day_key):
    """这一天的事件（按 day_events.ordinal）；表/库不存在返回空列表。"""
    from pathlib import Path as _Path
    if not _Path(str(db_path)).exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
    except Exception:
        return []
    try:
        rows = conn.execute(
            'SELECT event_id, ordinal, added_at FROM day_events'
            ' WHERE day_key=? ORDER BY ordinal', (str(day_key or ''),)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{'event_id': str(row[0]), 'ordinal': int(row[1] or 0),
             'added_at': row[2]} for row in rows]


def chain(db_path, limit=None):
    """事件链（按 seq）；prev_seq 指向前一件的 seq（0 = 链首）。"""
    from pathlib import Path as _Path
    if not _Path(str(db_path)).exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
    except Exception:
        return []
    try:
        sql = ('SELECT seq, event_id, prev_seq, added_at, reason'
               ' FROM event_chain ORDER BY seq')
        args = ()
        if limit:
            sql += ' LIMIT ?'
            args = (int(limit),)
        rows = conn.execute(sql, args).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{'seq': int(row[0]), 'event_id': str(row[1]),
             'prev_seq': int(row[2] or 0), 'added_at': row[3],
             'reason': str(row[4] or '')} for row in rows]


def stats(db_path):
    """{day_events, chain, days} —— dry-run 与体检共用的三个数。"""
    from pathlib import Path as _Path
    if not _Path(str(db_path)).exists():
        return {'day_events': 0, 'chain': 0, 'days': 0}
    try:
        conn = sqlite3.connect(str(db_path))
    except Exception:
        return {'day_events': 0, 'chain': 0, 'days': 0}
    try:
        day = conn.execute('SELECT COUNT(1) FROM day_events').fetchone()[0]
        ch = conn.execute('SELECT COUNT(1) FROM event_chain').fetchone()[0]
        days = conn.execute(
            'SELECT COUNT(DISTINCT day_key) FROM day_events').fetchone()[0]
        return {'day_events': int(day or 0), 'chain': int(ch or 0),
                'days': int(days or 0)}
    except Exception:
        return {'day_events': 0, 'chain': 0, 'days': 0}
    finally:
        conn.close()
