# -*- coding: utf-8 -*-
"""使用日志：只追加，不改记忆本体（2026-11-13 总则第 1、2 条）。

一行 = 某条记忆在某次交付里被真的用到了：
    session_id / entry_id / at / query_fp（查询指纹，可空）

为什么单独一张表：记忆本体"只增不改"，所以"被用过几次、最后一次是什么时候"
不能再写回 entries 的 hits / last_used。痕迹与内容分开存，读的时候合并看。

读取口径（兼容老数据）：
    effective_last_used = max(entries 里已有的时间列, 日志里的最新 at)
老库没有日志行时，就用列值；两者都没有的不筛（未知年龄不当作"很久没用"）。
"""
from __future__ import annotations

import sqlite3
import time

DDL = (
    'CREATE TABLE IF NOT EXISTS access_log ('
    ' id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, entry_id TEXT,'
    ' at REAL, query_fp TEXT)',
    'CREATE INDEX IF NOT EXISTS access_log_entry'
    ' ON access_log(session_id, entry_id, at)',
)


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    for ddl in DDL:
        conn.execute(ddl)
    return conn


def append(db_path, session_id, entry_ids, at=None, query_fp=''):
    """追加一批使用痕迹；只对真正被交付的命中行调用。返回追加行数。"""
    ids = [str(i) for i in (entry_ids or []) if str(i or '').strip()]
    if not ids:
        return 0
    stamp = float(at if at is not None else time.time())
    conn = _connect(db_path)
    try:
        conn.executemany(
            'INSERT INTO access_log (session_id, entry_id, at, query_fp)'
            ' VALUES (?,?,?,?)',
            [(str(session_id or ''), entry_id, stamp, str(query_fp or ''))
             for entry_id in ids])
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def latest_by_entry(db_path, session_id):
    """entry_id -> 最后一次被用到的时间；没有日志行返回空 dict。"""
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % str(db_path).replace('\\', '/'),
                               uri=True)
    except Exception:
        return {}
    try:
        rows = conn.execute(
            'SELECT entry_id, MAX(at) FROM access_log WHERE session_id=?'
            ' GROUP BY entry_id', (str(session_id or ''),)).fetchall()
        return {str(entry_id): float(stamp or 0.0) for entry_id, stamp in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def count(db_path, session_id=None):
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % str(db_path).replace('\\', '/'),
                               uri=True)
    except Exception:
        return 0
    try:
        if session_id is None:
            return int(conn.execute('SELECT COUNT(1) FROM access_log').fetchone()[0])
        return int(conn.execute('SELECT COUNT(1) FROM access_log WHERE session_id=?',
                                (str(session_id),)).fetchone()[0])
    except Exception:
        return 0
    finally:
        conn.close()
