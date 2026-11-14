# -*- coding: utf-8 -*-
"""压缩记录：DSH 官方压缩写下的摘要，收进 memory.db 的 compactions 表。

边界（与 docs/SEVEN-STEPS.md 一致：记忆层不写提示词、不调模型）：
  - 摘要正文是 DSH 官方压缩产出的，这里只负责**收下、落库、可召回**。
    本模块不调模型、不联网、不改写摘要一个字。
  - append-only：只有 INSERT 与 SELECT。同 (session_id, compaction_id) 二次写入
    被唯一约束挡掉（INSERT OR IGNORE），既有行永远不被 UPDATE / DELETE。
  - 老库自动建表：CREATE TABLE IF NOT EXISTS，第一次写入时才建；不动任何旧表。

时间窗（recall 挂回用，见 window_for / covering）：
    window_end   = compaction/summary 事件的 time（DSH 给毫秒，落库折成秒）
    window_start = 同一会话**上一条**压缩记录的 window_end
    第一次压缩没有上一条 → 退到"这个库里最早的条目 ts"（≈ 会话起点）
    两者都拿不到 → NULL；NULL 表示"判定不了"，recall 一律不挂（宁可少挂，不乱挂）

为什么"上一条的 window_end"是安全下界：DSH 的 shadowedRange 总接在上一次压缩的
summary 序号后面（本仓日志 24 条实测：4764 接 3774、129316 接 129315、123457 接
123456），所以两次压缩之间产生的消息**要么被这条遮住、要么还留在上一条的保留尾里**，
两者都是同一段对话。这条下界只可能把"上一条压缩完到这条压缩完"之间的消息算进来，
绝不会把更早（已被上一条遮住）的区间接进来。实测第 24 条的窗口正好是
129316–134380 这一段新消息。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TABLE = 'compactions'

# 只建不改：IF NOT EXISTS 让老库在第一次写入时自动补齐这张表。
DDL = (
    'CREATE TABLE IF NOT EXISTS %s ('
    ' id INTEGER PRIMARY KEY AUTOINCREMENT,'
    ' session_id TEXT NOT NULL,'
    ' compaction_id TEXT NOT NULL,'
    ' range_start INTEGER,'
    ' range_end INTEGER,'
    ' shadowed_count INTEGER,'
    ' shadowed_tokens INTEGER,'
    ' provider TEXT,'
    ' model TEXT,'
    ' text TEXT,'
    ' usage_json TEXT,'
    ' window_start REAL,'
    ' window_end REAL,'
    ' created_at REAL,'
    ' UNIQUE(session_id, compaction_id))' % TABLE,
    'CREATE INDEX IF NOT EXISTS idx_compactions_window'
    ' ON %s(session_id, window_end)' % TABLE,
)

COLUMNS = ('id', 'session_id', 'compaction_id', 'range_start', 'range_end',
           'shadowed_count', 'shadowed_tokens', 'provider', 'model', 'text',
           'usage_json', 'window_start', 'window_end', 'created_at')

# DSH 的 event.time 是毫秒；条目 ts / time.time() 是秒。超过这个量级一律按毫秒收。
_MS_FLOOR = 1e11


def db_path(root, session_id):
    """<root>/sessions/<session_id>/memory.db（与 SQL 库同一处，不另开文件）。"""
    from biocore_memory.store import sql_store
    return sql_store.db_path(root, session_id)


def _stamp(value):
    """事件时刻归一到秒；拿不到就 None（None 不是 0：判定不了 ≠ 零点）。"""
    try:
        number = float(value)
    except Exception:
        return None
    if number <= 0:
        return None
    return number / 1000.0 if number > _MS_FLOOR else number


def _int_or_none(value):
    try:
        return int(value)
    except Exception:
        return None


def summary_text(blocks):
    """把 summary 的 ContentBlock[] 拼成正文。

    只拼 text 块，块与块之间补一个换行；不折叠空白——摘要正文要能逐字比对，
    多空格也是原文的一部分（这与 textOfMessage 的归一化刻意相反）。
    """
    if isinstance(blocks, str):
        return blocks
    parts = []
    for block in blocks or []:
        if isinstance(block, dict) and block.get('type') == 'text' \
                and isinstance(block.get('text'), str):
            parts.append(block['text'])
        elif isinstance(block, str):
            parts.append(block)
    return '\n'.join(parts)


def _connect(path):
    conn = sqlite3.connect(str(path))
    for ddl in DDL:
        conn.execute(ddl)
    return conn


# 下界往前收一毫秒：窗口的左端是严格大于，不复位这一毫秒的话"会话第一条消息"
# 永远落在第一次压缩的窗口外。1 毫秒远小于任何两条消息的间隔，不会误吞上一条。
_EDGE_EPS = 1e-3


def _earliest_entry_ts(conn):
    """这个库最早一条记忆的 ts：第一次压缩没有上一条压缩可参照时的下界。"""
    try:
        row = conn.execute(
            'SELECT MIN(ts) FROM entries WHERE ts IS NOT NULL AND ts > 0').fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    stamp = _stamp(row[0])
    return None if stamp is None else stamp - _EDGE_EPS


def _previous_window_end(conn, session_id, window_end):
    """时间上紧挨着的前一条，而不是最后插入的那条。

    回填顺序不必等于事件顺序（比如先把最近几次补进去、再补更早的），所以
    "上一条"按 window_end 找，不按自增 id 找；这样任何插入顺序都得到同一个窗。
    同一条重复写入时它有同一刻的 window_end，严格小于把兄弟行排除在外。
    """
    row = conn.execute(
        'SELECT window_end FROM %s'
        ' WHERE session_id=? AND window_end IS NOT NULL AND window_end < ?'
        ' ORDER BY window_end DESC LIMIT 1' % TABLE,
        (str(session_id or ''), float(window_end))).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _normalise(payload):
    """入参归一：node / 回填工具都用 snake_case；camelCase 也认（宿主直传时）。"""
    data = payload if isinstance(payload, dict) else {}

    def pick(*names):
        for name in names:
            if data.get(name) not in (None, ''):
                return data[name]
        return None

    range_raw = pick('shadowed_range', 'shadowedRange') or {}
    range_raw = range_raw if isinstance(range_raw, dict) else {}
    range_start = pick('range_start')
    range_end = pick('range_end')
    if range_start is None:
        range_start = range_raw.get('start')
    if range_end is None:
        range_end = range_raw.get('end')
    seqs = pick('shadowed_seqs', 'shadowedSeqs')
    count = pick('shadowed_count', 'shadowedCount')
    if count is None and isinstance(seqs, list):
        count = len(seqs)
    usage = pick('usage')
    if isinstance(usage, str):
        usage_json = usage
    elif usage is None:
        usage_json = None
    else:
        try:
            usage_json = json.dumps(usage, ensure_ascii=False, sort_keys=True)
        except Exception:
            usage_json = None
    text = pick('text')
    if text is None:
        text = summary_text(pick('summary'))
    return {
        'compaction_id': str(pick('compaction_id', 'compactionId') or '').strip(),
        'range_start': _int_or_none(range_start),
        'range_end': _int_or_none(range_end),
        'shadowed_count': _int_or_none(count),
        'shadowed_tokens': _int_or_none(pick('shadowed_tokens', 'shadowedTokenCount')),
        'provider': str(pick('provider') or ''),
        'model': str(pick('model') or ''),
        'text': '' if text is None else str(text),
        'usage_json': usage_json,
        'covered_at': _stamp(pick('covered_at', 'time', 'ts')),
    }


def record(db_path, session_id, payload=None, now=None):
    """收下一条压缩记录。返回 {recorded, skipped, id, reason}。

    幂等由 UNIQUE(session_id, compaction_id) 保证；重复写入既不新增行，
    也不改旧行（append-only），返回值里 recorded=0、reason='duplicate'。
    """
    row = _normalise(payload)
    session_id = str(session_id or '').strip()
    if not row['compaction_id']:
        return {'recorded': 0, 'skipped': 1, 'id': None,
                'reason': 'no_compaction_id'}
    if not session_id:
        return {'recorded': 0, 'skipped': 1, 'id': None, 'reason': 'no_session'}
    path = Path(str(db_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = float(now if now is not None else time.time())
    conn = _connect(path)
    try:
        window_end = row['covered_at'] if row['covered_at'] is not None else stamp
        window_start = _previous_window_end(conn, session_id, window_end)
        if window_start is None:
            window_start = _earliest_entry_ts(conn)
        cur = conn.execute(
            'INSERT OR IGNORE INTO %s'
            ' (session_id, compaction_id, range_start, range_end, shadowed_count,'
            '  shadowed_tokens, provider, model, text, usage_json,'
            '  window_start, window_end, created_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)' % TABLE,
            (session_id, row['compaction_id'], row['range_start'], row['range_end'],
             row['shadowed_count'], row['shadowed_tokens'], row['provider'],
             row['model'], row['text'], row['usage_json'],
             window_start, window_end, stamp))
        conn.commit()
        inserted = 1 if cur.rowcount > 0 else 0
        new_id = cur.lastrowid if inserted else None
        if not inserted:
            hit = conn.execute(
                'SELECT id FROM %s WHERE session_id=? AND compaction_id=?' % TABLE,
                (session_id, row['compaction_id'])).fetchone()
            new_id = int(hit[0]) if hit else None
    finally:
        conn.close()
    return {'recorded': inserted, 'skipped': 0 if inserted else 1,
            'id': new_id, 'reason': '' if inserted else 'duplicate'}


def _row_dict(row):
    return dict(zip(COLUMNS, row))


def load(db_path, session_id, limit=None):
    """按写入顺序读出这个会话的压缩记录（recall 与检查用）。"""
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
        return [_row_dict(row) for row in conn.execute(sql, tuple(args))]
    except Exception as exc:
        logger.debug('[compaction] 读失败：%s: %s', type(exc).__name__, str(exc)[:120])
        return []
    finally:
        conn.close()


def windows(db_path, session_id):
    """这个会话里所有可判定的时间窗（窗口缺边的不在内）。

    召回路径一次取全部，再在内存里比对：命中可能有 k 条，逐条查库就是 k 次往返。
    """
    return [row for row in load(db_path, session_id)
            if row.get('window_start') is not None
            and row.get('window_end') is not None]


def covering(db_path, session_id, ts):
    """哪一条压缩记录的时间窗盖住了 ts；判定不了（窗口缺边）一律返回 None。"""
    stamp = _stamp(ts)
    if stamp is None:
        return None
    hit = None
    for row in windows(db_path, session_id):
        if float(row['window_start']) < stamp <= float(row['window_end']):
            if hit is None or float(row['window_end']) > float(hit['window_end']):
                hit = row
    return hit
