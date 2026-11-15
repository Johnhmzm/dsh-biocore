# -*- coding: utf-8 -*-
"""日总结（第四层）：把当天的若干条「事件总结」再总结一次，每 8 条一篇。

为什么是"再总结"而不是拼接：8 条事件总结拼起来还是一堆细节，第四层的用处是
"这一天大概发生了什么"。所以每 8 条事件开一次模型调用，产出 1 段日总结；
当天不足 8 条也生成一条（宁可只有一句，也不要没有）。

只增不改：
  - day_summaries 表 CREATE TABLE IF NOT EXISTS，写入 INSERT OR IGNORE；
  - 唯一键 (day_key, chunk_index)，同一天重跑第二遍 0 写入；
  - 每天的分块是对当天的有序事件做 8 条切片，切片边界稳定（按 day_events.ordinal）。

本模块不自己调模型：summarizer 由调用方注入（和「沉」一致）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TABLE = 'day_summaries'
CHUNK_EVENTS = 8
PROMPT_VERSION = 'day-v1'

DDL = (
    'CREATE TABLE IF NOT EXISTS day_summaries ('
    ' day_key TEXT NOT NULL,'
    ' chunk_index INTEGER NOT NULL,'
    ' event_ids TEXT,'
    ' text TEXT,'
    ' model TEXT,'
    ' created_at REAL,'
    ' UNIQUE(day_key, chunk_index))',
    'CREATE INDEX IF NOT EXISTS idx_day_summaries_day ON day_summaries(day_key)',
)

RULES = """下面是一个人在同一天里的若干条事件总结（每条一行）。
把它们再总结成一段话：这一天大概发生了什么、和谁、有什么变化。
要求：
1. 一段话，40–120 字；不要罗列、不要逐条复述、不要编号。
2. 只用给出的信息，不补细节、不猜动机；信息不足就写「未明确」。
3. 人称按原文（自己写「我」，对方写全名），不要用「你」。
"""


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    return conn


def build_prompt(lines):
    body = '\n'.join('- ' + str(line or '').strip() for line in (lines or [])
                     if str(line or '').strip())
    if not body:
        return ''
    return RULES + '\n事件总结：\n' + body


def chunks(items, size=CHUNK_EVENTS):
    """按 size 切片；空列表给空。"""
    rows = list(items or [])
    size = max(1, int(size or CHUNK_EVENTS))
    return [rows[start:start + size] for start in range(0, len(rows), size)]


def summary_lines(conn, day_key):
    """当天的事件总结（按 day_events.ordinal 排），返回 [(event_id, text)]。

    事件总结的空缺用 overview 顶上（两条都没有就跳过这一条）。
    """
    out = []
    try:
        rows = conn.execute(
            'SELECT d.event_id, e.overview, e.text FROM day_events d'
            ' LEFT JOIN events e ON e.id = d.event_id'
            ' WHERE d.day_key=? ORDER BY d.ordinal', (str(day_key or ''),)).fetchall()
    except Exception:
        return out
    for event_id, overview, text in rows:
        body = str(overview or '').strip() or str(text or '').strip()
        if body:
            out.append((str(event_id), body))
    return out


def done_chunks(db_path, day_key):
    """这一天已经写过日总结的分块下标（幂等判断与续跑用）。"""
    path = Path(str(db_path))
    if not path.exists():
        return set()
    try:
        conn = sqlite3.connect(str(path))
    except Exception:
        return set()
    try:
        rows = conn.execute('SELECT chunk_index FROM day_summaries WHERE day_key=?',
                            (str(day_key or ''),)).fetchall()
    except Exception:
        return set()
    finally:
        conn.close()
    return {int(row[0]) for row in rows}


def plan(db_path, day_key, size=CHUNK_EVENTS):
    """盘点这一天：几块、哪几块还没写。只读。"""
    path = Path(str(db_path))
    info = {'day_key': day_key, 'events': 0, 'chunks': 0, 'done': 0,
            'pending': 0, 'plans': []}
    if not path.exists():
        return info
    try:
        conn = sqlite3.connect(str(path))
    except Exception:
        return info
    try:
        lines = summary_lines(conn, day_key)
    finally:
        conn.close()
    blocks = chunks(lines, size)
    done = done_chunks(db_path, day_key)
    info['events'] = len(lines)
    info['chunks'] = len(blocks)
    info['done'] = len([i for i in range(len(blocks)) if i in done])
    for index, block in enumerate(blocks):
        if index in done:
            continue
        info['pending'] += 1
        info['plans'].append((index, block))
    return info


def generate(db_path, day_key, summarizer, model='', now=None, size=CHUNK_EVENTS,
             cache_dir=None, limit=0):
    """给这一天还没写过的分块生成日总结（幂等）。

    返回 {'day_key', 'written', 'skipped', 'chunks', 'cached', 'reason'}。
    """
    info = plan(db_path, day_key, size=size)
    out = {'day_key': day_key, 'written': 0, 'skipped': 0,
           'chunks': info['chunks'], 'cached': 0, 'reason': ''}
    if not info['plans']:
        out['reason'] = 'nothing_pending'
        return out
    if summarizer is None:
        out['reason'] = 'no_summarizer'
        return out
    stamp = float(now if now is not None else time.time())
    conn = _connect(db_path)
    try:
        for index, block in info['plans']:
            if limit and out['written'] >= int(limit):
                break
            prompt = build_prompt([text for _eid, text in block])
            if not prompt:
                continue
            try:
                if cache_dir is None:
                    answer, cached = summarizer(prompt), False
                else:
                    from biocore_memory.store import summary_cache as _cache
                    got = _cache.call(summarizer, prompt, cache_dir, model,
                                      version=PROMPT_VERSION,
                                      content='%s|%s|%d\n%s' % (
                                          PROMPT_VERSION, day_key, index, prompt))
                    answer, cached = got.get('text'), bool(got.get('cached'))
            except Exception as exc:
                logger.warning('[day] 日总结失败（%s）：%s',
                               type(exc).__name__, str(exc)[:120])
                out['reason'] = 'error:%s' % type(exc).__name__
                break
            text = str(answer or '').strip()
            if not text:
                out['skipped'] += 1
                continue
            cur = conn.execute(
                'INSERT OR IGNORE INTO day_summaries'
                ' (day_key, chunk_index, event_ids, text, model, created_at)'
                ' VALUES (?,?,?,?,?,?)',
                (str(day_key), int(index),
                 json.dumps([eid for eid, _t in block], ensure_ascii=False),
                 text, str(model or ''), stamp))
            conn.commit()
            if cur.rowcount > 0:
                out['written'] += 1
                out['cached'] += 1 if cached else 0
            else:
                out['skipped'] += 1
    finally:
        conn.close()
    return out


def days(db_path):
    """有事件的天（按日期升序），供 00:00 结算用。"""
    path = Path(str(db_path))
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
    except Exception:
        return []
    try:
        rows = conn.execute(
            'SELECT day_key, COUNT(1) FROM day_events GROUP BY day_key'
            " ORDER BY day_key").fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{'day_key': str(row[0]), 'events': int(row[1] or 0)} for row in rows]


def load(db_path, day_key, limit=None):
    """读回日总结（交付用）：[{day_key, chunk_index, text, event_ids}]。"""
    path = Path(str(db_path))
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
    except Exception:
        return []
    try:
        sql = ('SELECT day_key, chunk_index, event_ids, text, model, created_at'
               ' FROM day_summaries WHERE day_key=? ORDER BY chunk_index')
        args = [str(day_key or '')]
        if limit:
            sql += ' LIMIT ?'
            args.append(int(limit))
        rows = conn.execute(sql, tuple(args)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return [{'day_key': str(row[0]), 'chunk_index': int(row[1] or 0),
             'event_ids': json.loads(row[2] or '[]'), 'text': str(row[3] or ''),
             'model': str(row[4] or ''), 'created_at': row[5]} for row in rows]
