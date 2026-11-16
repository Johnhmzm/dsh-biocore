# -*- coding: utf-8 -*-
"""改：纠正边 —— 同一 (subject, attr) 里新值顶掉旧值时，把这次更正写成一条边。

为什么要有边：claims 表本身是"合并后的现状"（谁新、谁被顶掉），它答不了
"改过几次、从什么改成什么、哪一次是直接陈述"。边是 append-only 的变更史，
交付时可以直接读边、不用每次重算。

三条纪律：
  - 只增不改：表 CREATE TABLE IF NOT EXISTS，写入 INSERT OR IGNORE；不 UPDATE、不 DELETE；
  - 幂等：(session_id, subject, attr, from_value, to_value) 唯一，重跑第二次 0 写入；
  - 不调模型、不判断：哪些值算"被顶掉"由 claims.merge_claims 已经算好，这里只落盘。
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TABLE = 'claim_edges'
KIND_SUPERSEDE = 'supersede'

DDL = (
    'CREATE TABLE IF NOT EXISTS %s ('
    ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' session_id TEXT NOT NULL,'
    ' subject TEXT,'
    ' attr TEXT,'
    ' from_value TEXT,'
    ' to_value TEXT,'
    ' from_turn INTEGER,'
    ' to_turn INTEGER,'
    ' kind TEXT,'
    ' source TEXT,'
    ' created_at REAL,'
    ' UNIQUE(session_id, subject, attr, from_value, to_value))' % TABLE,
    'CREATE INDEX IF NOT EXISTS idx_claim_edges_key'
    ' ON %s(session_id, subject, attr)' % TABLE,
)

COLUMNS = ('id', 'session_id', 'subject', 'attr', 'from_value', 'to_value',
           'from_turn', 'to_turn', 'kind', 'source', 'created_at')


def _connect(path):
    conn = sqlite3.connect(str(path), timeout=30.0)     # M-28: shared-store lock
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    for ddl in DDL:
        conn.execute(ddl)
    return conn


def plan(table):
    """从 merge_claims 的表算出该写哪些边：老值 -> 这一键最新的那个值。

    只写"确实被新值顶掉"的：最新值本身不写边；同一键超过 2 个值时，
    每个老值各写一条指向最新值的边（谁顶谁的顺序不猜，一律指向当前值）。
    """
    out = []
    for key, slot in (table or {}).items():
        values = [row for row in (slot.get('values') or []) if isinstance(row, dict)]
        if len(values) < 2:
            continue
        newest = max(values, key=lambda row: (int(row.get('turn') or 0),
                                              len(str(row.get('value') or ''))))
        for row in values:
            if row is newest:
                continue
            old_value = str(row.get('value') or '').strip()
            new_value = str(newest.get('value') or '').strip()
            if not old_value or not new_value or old_value == new_value:
                continue
            out.append({'subject': str(key).split('·')[0],
                        'attr': str(key).split('·')[-1] if '·' in str(key) else '',
                        'from_value': old_value, 'to_value': new_value,
                        'from_turn': int(row.get('turn') or 0),
                        'to_turn': int(newest.get('turn') or 0),
                        'kind': (KIND_SUPERSEDE
                                 if str(row.get('status') or '') == 'superseded'
                                 else 'revise'),
                        'source': str(newest.get('source') or '')})
    return out


def record(db_path, session_id, table, now=None):
    """把计划里的边写进库（幂等）。返回 {planned, inserted, skipped}。"""
    planned = plan(table)
    if not planned:
        return {'planned': 0, 'inserted': 0, 'skipped': 0}
    path = Path(str(db_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = float(now if now is not None else time.time())
    conn = _connect(path)
    inserted = 0
    try:
        for edge in planned:
            cur = conn.execute(
                'INSERT OR IGNORE INTO %s'
                ' (session_id, subject, attr, from_value, to_value, from_turn,'
                '  to_turn, kind, source, created_at)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?)' % TABLE,
                (str(session_id or ''), edge['subject'], edge['attr'],
                 edge['from_value'], edge['to_value'], edge['from_turn'],
                 edge['to_turn'], edge['kind'], edge['source'], stamp))
            inserted += 1 if cur.rowcount > 0 else 0
        conn.commit()
    finally:
        conn.close()
    return {'planned': len(planned), 'inserted': inserted,
            'skipped': len(planned) - inserted}


def edges(db_path, session_id, limit=None):
    """读回这个会话的纠正边（按写入顺序）；库或表不存在都返回空列表。"""
    path = Path(str(db_path))
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
    except Exception:
        return []
    try:
        sql = ('SELECT %s FROM %s WHERE session_id=? ORDER BY id'
               % (', '.join(COLUMNS), TABLE))
        args = [str(session_id or '')]
        if limit:
            sql += ' LIMIT ?'
            args.append(int(limit))
        rows = conn.execute(sql, tuple(args)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [dict(zip(COLUMNS, row)) for row in rows]


def lines(rows, limit=4):
    """交付用的一行一条边：不并列、只陈述"改成了什么"。"""
    out = []
    for row in (rows or [])[:max(0, int(limit))]:
        subject = str(row.get('subject') or '').strip()
        attr = str(row.get('attr') or '').strip()
        label = ('%s·%s' % (subject, attr)).strip('·')
        out.append('- %s：%s → %s' % (label, row.get('from_value'),
                                      row.get('to_value')))
    return out
