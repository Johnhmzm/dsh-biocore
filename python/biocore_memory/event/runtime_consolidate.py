# -*- coding: utf-8 -*-
"""沉：把还没结算的行登记成"待总结块"（运行期，turn_end 的写入路径调用）。

语义判断由模型给，调度与落库由这里给（docs/SEVEN-STEPS.md 边界）：
  - **有摘要器就调它，这正是沉的核心动作**：一次调用产出 overview 与 claims，
    写进 `event_consolidation` 与事件本体。调用用的是 claims 模块既有的提示词与解析器。
  - 不做的部分只有判断：哪个说法更新、算不算同一件事（thread）、措辞——那些留给总结层，
    这里 thread 留空、status 原样保留、不改写。
  - 机械可得的部分本地算：day 由 ts 推、ordinal 取该日 max+1、起止取成员轮次。
  - 开关 `recall.consolidate_on`，缺省 0：关着一行不写，连表都不建。
  - 没有摘要器不维护、不抛：code=no_summarizer（结构照登，语义留空）。
  - 幂等：同一块第二次跑不新增行、不改内容（块键由成员身份+文本决定）。
  - 失败降级：原文照留、登记 attempts+1、打一行 warn（带异常类型），绝不阻塞 turn_end。
  - 重试：同一块最多 3 次，退避 1m / 10m / 1h，超限标 failed。

状态只放 SQL 表 `event_consolidation`（写入只走 SQL 是硬规矩；状态与计数同源）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

SWITCH = 'consolidate_on'
MAX_ATTEMPTS = 3
BACKOFF_S = (60.0, 600.0, 3600.0)
STATUS_RECORDED = 'recorded'
STATUS_FAILED = 'failed'
CODE_NO_SUMMARIZER = 'no_summarizer'
_CACHE_SUBDIR = ('cache', 'summary')
CODE_AWAITING = 'awaiting_summary'

DDL = (
    'CREATE TABLE IF NOT EXISTS event_consolidation ('
    ' block_key TEXT PRIMARY KEY, session_id TEXT,'
    ' status TEXT, code TEXT, attempts INTEGER,'
    ' first_at REAL, updated_at REAL, next_at REAL,'
    ' members INTEGER, first_turn INTEGER, last_turn INTEGER,'
    ' day TEXT, ordinal INTEGER, thread TEXT, overview TEXT, claims TEXT,'
    ' event_id TEXT)',
)


def enabled(payload=None, store=None, env=None):
    """开关：config(recall.consolidate_on) > env > 缺省 0。读不到一律当关。"""
    try:
        from biocore_memory.config import recall_params as _rp
        return int(_rp.resolve(SWITCH, payload, env)) == 1
    except Exception as exc:
        logger.warning('[consolidate] 开关解析失败，按关闭处理：%s: %s',
                       type(exc).__name__, str(exc)[:120])
        return False


def _turn(row, index=0):
    try:
        value = int((row or {}).get('turn') or 0)
    except Exception:
        value = 0
    return value if value > 0 else index + 1


def settled_end(events):
    """已结算到第几轮：现有事件的最大 end；没有事件就是 0。"""
    ends = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        try:
            ends.append(int(event.get('end') or 0))
        except Exception:
            continue
    return max(ends) if ends else 0


def pending_rows(entries, events):
    """待结算块：turn 超过已结算末尾的行，按 (ts, 轮次) 排。"""
    settled = settled_end(events)
    rows = [row for index, row in enumerate(entries or [])
            if isinstance(row, dict) and _turn(row, index) > settled]
    rows.sort(key=lambda row: (float(row.get('ts') or 0.0), _turn(row)))
    return rows


def block_key(session_id, rows):
    """块键：会话 + 成员身份（id/turn/ts/text）。同一批行永远同一个键。"""
    digest = hashlib.sha256()
    digest.update(str(session_id or '').encode('utf-8'))
    for row in rows or []:
        digest.update(('|'.join([str(row.get('id') or ''), str(_turn(row)),
                                 str(float(row.get('ts') or 0.0)),
                                 str(row.get('text') or '')])).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()[:32]


def _block_content(rows, day):
    """缓存键的内容部分：日期 + 成员原文（与 _ask 用的完全同一份）。"""
    body = '\n'.join(str(row.get('text') or '') for row in rows or [])
    return '%s\n%s' % (str(day or ''), body)


def _cache_probe(db_path, rows, day, model):
    """查一下这块的缓存在不在（只读，不发请求）；用于"已结算"时如实报 cached。"""
    try:
        from pathlib import Path as _Path
        from biocore_memory.store import summary_cache as _cache
        root_dir = _Path(str(db_path)).parents[2]
        cache_dir = root_dir.joinpath(*_CACHE_SUBDIR)
        t0 = time.time()
        hit = _cache.read(cache_dir, _block_content(rows, day), model)
        return {'cached': hit is not None,
                'cache_ms': round((time.time() - t0) * 1000, 1)}
    except Exception:
        return {'cached': False, 'cache_ms': 0.0}


def _ask(summarizer, rows, day, cache_dir=None, model=''):
    """调一次摘要器：产出 overview 与 claims（复用 claims 的既有提示词与解析器）。

    带缓存：内容指纹 + 模型名 + 提示词版本命中就不再发请求（同一批行第二次必命中）。
    只调一次、只读一次结果；判断哪条更准、算不算同一件事都不在这里做。
    """
    from biocore_memory.event import claims as _claims
    from biocore_memory.store import summary_cache as _cache
    body = '\n'.join(str(row.get('text') or '') for row in rows or [])
    turns = [_turn(row, index) for index, row in enumerate(rows)]
    prompt = _claims.claims_prompt({'text': body,
                                    'start': min(turns) if turns else 0,
                                    'end': max(turns) if turns else 0},
                                   known=[], day=day, threads=[])
    if cache_dir is None:
        answer = summarizer(prompt)
        cached, spent = False, 0.0
    else:
        got = _cache.call(summarizer, prompt, cache_dir, model,
                          content='%s\n%s' % (str(day or ''), body))
        answer, cached, spent = got['text'], got['cached'], got['ms']
    overview, claims = _claims.parse_claims(answer, day=day)
    return overview, claims, {'cached': bool(cached), 'ms': spent}


def _day_of(rows):
    """日期机械可得（第一行的本地日期）；不判断，不做语义。"""
    from biocore_memory.event import fourlayer
    return fourlayer.event_day({'entries': list(rows or [])})


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    for ddl in DDL:
        conn.execute(ddl)
    # 旧库补列（可加迁移）：表可能是上一版建的，缺 claims 就加上，绝不重建。
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(event_consolidation)')}
        if 'claims' not in cols:
            conn.execute('ALTER TABLE event_consolidation ADD COLUMN claims TEXT')
    except Exception as exc:
        logger.warning('[consolidate] 补 claims 列失败：%s: %s',
                       type(exc).__name__, str(exc)[:120])
    return conn


def _read_state(conn, key):
    row = conn.execute(
        'SELECT status, code, attempts, next_at FROM event_consolidation'
        ' WHERE block_key=?', (key,)).fetchone()
    if not row:
        return None
    return {'status': row[0], 'code': row[1], 'attempts': int(row[2] or 0),
            'next_at': float(row[3] or 0.0)}


def _write_state(conn, key, session_id, status, code, attempts, first_at, now,
                 next_at, members, first_turn, last_turn, day, ordinal=0,
                 thread='', overview='', claims_json=None, event_id=None):
    conn.execute(
        'INSERT INTO event_consolidation'
        ' (block_key, session_id, status, code, attempts, first_at, updated_at,'
        '  next_at, members, first_turn, last_turn, day, ordinal, thread, overview,'
        '  claims, event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
        ' ON CONFLICT(block_key) DO UPDATE SET status=excluded.status,'
        ' code=excluded.code, attempts=excluded.attempts, updated_at=excluded.updated_at,'
        ' next_at=excluded.next_at, members=excluded.members,'
        ' first_turn=excluded.first_turn, last_turn=excluded.last_turn,'
        ' day=excluded.day',
        (key, str(session_id or ''), status, str(code or ''), int(attempts),
         float(first_at or now), float(now), float(next_at), int(members),
         int(first_turn), int(last_turn), str(day or ''), int(ordinal),
         str(thread or ''), str(overview or ''), claims_json, event_id))


def _existing_ordinals(conn, day):
    try:
        return [int(row[0]) for row in conn.execute(
            'SELECT ordinal FROM events_fourlayer WHERE day=?', (day,))]
    except Exception:
        return []


def _commit_event(conn, session_id, key, rows, events, overview, claims, day, stamp):
    """写事件本体 + 四层行。day/ordinal 本地算（不看模型），thread 留空。"""
    from pathlib import Path
    from biocore_memory.event import fourlayer
    from biocore_memory.event import fourlayer_migrate as _mig
    from biocore_memory.store import sql_store

    turns = [_turn(row, index) for index, row in enumerate(rows)]
    event = {'event_id': 'EV%s' % str(key)[:10],
             'start': min(turns) if turns else 0,
             'end': max(turns) if turns else 0,
             'members': [str(row.get('id') or '') for row in rows],
             'text': '\n'.join(str(row.get('text') or '') for row in rows),
             'overview': str(overview or '').strip(),
             'thread': '',
             'claims': [dict(item, turn=max(turns) if turns else 0)
                        for item in (claims or [])],
             'closed_ts': 0.0,
             'entries': [dict(row) for row in rows]}
    # 老库可能还没有四层表：按迁移器的 DDL 就地建（幂等，不改旧表结构）
    for ddl in _mig.FOURLAYER_DDL:
        conn.execute(ddl)
    ordinal = fourlayer.next_ordinal(_existing_ordinals(conn, day))
    mapping = fourlayer.map_event(event, ordinal=ordinal)
    root = Path(conn.execute('PRAGMA database_list').fetchone()[2]).parents[2]
    seen = max([int(e.get('end') or 0) for e in events or [] if isinstance(e, dict)] or [0])
    sql_store.save_event_state(str(root), session_id, seen,
                               list(events or []) + [event], {}, [])
    conn.execute('INSERT INTO events_fourlayer'
                 ' (event_id, day, ordinal, thread, fingerprint, updated_at)'
                 ' VALUES (?,?,?,?,?,?)'
                 ' ON CONFLICT(event_id) DO UPDATE SET day=excluded.day,'
                 ' ordinal=excluded.ordinal, thread=excluded.thread,'
                 ' fingerprint=excluded.fingerprint, updated_at=excluded.updated_at',
                 (event['event_id'], mapping['day'], mapping['ordinal'],
                  mapping['thread'], _mig.fingerprint(mapping), stamp))
    conn.execute('DELETE FROM event_sentences WHERE event_id=?', (event['event_id'],))
    conn.executemany('INSERT INTO event_sentences'
                     ' (event_id, sentence_index, turn, ts, speaker, text)'
                     ' VALUES (?,?,?,?,?,?)',
                     [(s['event_id'], s['sentence_index'], s['turn'], s['ts'],
                       s['speaker'], s['text']) for s in mapping['sentences']])
    return {'event_id': event['event_id'], 'ordinal': mapping['ordinal']}


def _portrait_on(db_path):
    """画像开关：config(recall.portrait_on) > env > 0。"""
    try:
        from biocore_memory.config import recall_params as _rp
        from pathlib import Path as _Path
        root_dir = _Path(str(db_path)).parents[2]
        return int(_rp.resolve('portrait_on', {'recall': {}}, None)) == 1 or \
            _portrait_env(root_dir)
    except Exception:
        return False


def _portrait_env(root_dir):
    import os
    return str(os.environ.get('BIOCORE_PORTRAIT_ON') or '').strip().lower() in ('1', 'true', 'on', 'yes')


def _maybe_portrait(db_path, session_id, event_id, overview, summarizer, cache_dir, model):
    """事件结算后落一条画像：内容由总结层给，记忆侧只追加、不改。

    失败只留一行 warn，不影响事件结算本身。
    """
    try:
        if summarizer is None or not _portrait_on(db_path):
            return {'enabled': False}
        from pathlib import Path as _Path
        from biocore_memory.identity import portrait_store as _ps
        from biocore_memory.store import summary_cache as _cache
        material = '画像:%s' % str(overview or '')
        prompt = ('写完这一条关于这个人的画像，一句话，只输出这句话。\n'
                  '这次发生的事：%s' % str(overview or ''))
        got = _cache.call(summarizer, prompt, cache_dir, model,
                          version='portrait-v1', content=material)
        text = str(got.get('text') or '').strip()
        if not text:
            return {'enabled': True, 'written': 0, 'cached': got.get('cached')}
        session_dir = _Path(str(db_path)).parent
        written = _ps.append(session_dir, [{'action': 'add', 'text': text,
                                           'evidence': [str(event_id)]}])
        return {'enabled': True, 'written': written, 'cached': got.get('cached'),
                'text_len': len(text)}
    except Exception as exc:
        logger.warning('[portrait] 落盘失败（%s）：%s', type(exc).__name__, str(exc)[:120])
        return {'enabled': True, 'error': type(exc).__name__}


def _record_failure(conn, key, session_id, rows, state, exc, stamp,
                    use_rows, day):
    """失败降级：attempts+1、按 1m/10m/1h 退避、3 次后 failed。

    补偿写自己也可能失败（磁盘满、库被锁）：那也只留一行 warn，
    绝不把异常抛回 turn_end。
    """
    attempts = (int(state['attempts']) if state else 0) + 1
    failed = attempts >= MAX_ATTEMPTS
    backoff = 0.0 if failed else BACKOFF_S[min(attempts - 1, len(BACKOFF_S) - 1)]
    first_turn, last_turn = use_rows
    try:
        _write_state(conn, key, session_id,
                     STATUS_FAILED if failed else STATUS_RECORDED,
                     type(exc).__name__, attempts, stamp, stamp, stamp + backoff,
                     len(rows), first_turn, last_turn, day)
        conn.commit()
    except Exception as write_exc:
        logger.warning('[consolidate] 块 %s 连失败记录都写不进去：%s: %s',
                       key[:12], type(write_exc).__name__, str(write_exc)[:120])
    logger.warning('[consolidate] 块 %s 结算失败（第 %d 次，%s）：%s',
                   key[:12], attempts, type(exc).__name__, str(exc)[:120])
    return {'enabled': True, 'pending': len(rows), 'recorded': 0,
            'failed': failed, 'code': type(exc).__name__, 'attempts': attempts,
            'block_key': key}


def run(db_path, session_id, entries, events, summarizer=True, now=None, model=''):
    """登记一个待总结块；返回报告。

    关着的时候**什么都不做**（不建表、不写行）——这是缺省 0 的证据面。
    `summarizer` 只用来决定 code（有没有人接活），这里绝不调用它。
    """
    stamp = float(now if now is not None else time.time())
    rows = pending_rows(entries, events)
    if not rows:
        return {'enabled': True, 'pending': 0, 'recorded': 0}
    key = block_key(session_id, rows)
    turns = [_turn(row, index) for index, row in enumerate(rows)]
    conn = _connect(db_path)
    try:
        state = _read_state(conn, key)
        if state and state['status'] == STATUS_FAILED:
            return {'enabled': True, 'pending': len(rows), 'recorded': 0,
                    'skipped': 'failed', 'block_key': key}
        # 登记成功且没失败过 = 终态（幂等）；带 attempts>0 的登记是"写失败过"，
        # 还要按退避继续重试，直到 3 次后标 failed。
        if state and state['status'] == STATUS_RECORDED and int(state['attempts']) == 0:
            # 已经结算过：一个请求都不发；缓存命中就如实报 cached（毫秒级）
            probe = _cache_probe(db_path, rows, _day_of(rows), str(model or ''))
            return {'enabled': True, 'pending': len(rows), 'recorded': 0,
                    'skipped': 'recorded', 'block_key': key,
                    'cached': probe['cached'], 'cache_ms': probe['cache_ms']}
        if state and float(state['next_at'] or 0.0) > stamp:
            return {'enabled': True, 'pending': len(rows), 'recorded': 0,
                    'skipped': 'backoff', 'block_key': key,
                    'next_at': state['next_at']}
        day = _day_of(rows)
        if summarizer is None:
            # 没有人接活：只登记结构，语义留空（code 说明原因）
            try:
                _write_state(conn, key, session_id, STATUS_RECORDED, CODE_NO_SUMMARIZER,
                             0, stamp, stamp, stamp + BACKOFF_S[0], len(rows),
                             min(turns), max(turns), day)
                conn.commit()
            except Exception as exc:
                return _record_failure(conn, key, session_id, rows, state, exc, stamp,
                                       use_rows=(min(turns), max(turns)), day=day)
            logger.warning('[consolidate] 块 %s 已登记（no_summarizer，%d 行）',
                           key[:12], len(rows))
            return {'enabled': True, 'pending': len(rows), 'recorded': 1,
                    'code': CODE_NO_SUMMARIZER, 'block_key': key, 'attempts': 0,
                    'day': day, 'first_turn': min(turns), 'last_turn': max(turns)}
        # 有摘要器：沉的核心动作——调一次，把 overview 与 claims 落下来。
        try:
            from pathlib import Path as _Path
            root_dir = _Path(str(db_path)).parents[2]
            cache_dir = root_dir.joinpath(*_CACHE_SUBDIR)
            overview, claims, cache_info = _ask(summarizer, rows, day,
                                                cache_dir=cache_dir,
                                                model=str(model or ''))
            written = _commit_event(conn, session_id, key, rows, events,
                                    overview, claims, day, stamp)
            _write_state(conn, key, session_id, STATUS_RECORDED, '', 0, stamp, stamp,
                         stamp + BACKOFF_S[0], len(rows), min(turns), max(turns),
                         day, ordinal=written['ordinal'], overview=overview,
                         claims_json=json.dumps(claims, ensure_ascii=False),
                         event_id=written['event_id'])
            conn.commit()
            portrait_info = _maybe_portrait(db_path, session_id, written['event_id'],
                                            overview, summarizer, cache_dir, model)
            logger.info('[consolidate] 块 %s 结算完成：事件 %s，%d 条 claims（cached=%s）',
                        key[:12], written['event_id'], len(claims), cache_info['cached'])
            return {'enabled': True, 'pending': len(rows), 'recorded': 1,
                    'code': 'summarized', 'block_key': key, 'attempts': 0,
                    'cached': cache_info['cached'], 'cache_ms': cache_info['ms'],
                    'day': day, 'ordinal': written['ordinal'],
                    'event_id': written['event_id'],
                    'overview_chars': len(overview or ''),
                    'claims': len(claims),
                    'first_turn': min(turns), 'last_turn': max(turns),
                    'portrait': portrait_info}
        except Exception as exc:
            return _record_failure(conn, key, session_id, rows, state, exc, stamp,
                                   use_rows=(min(turns), max(turns)), day=day)
        except Exception as exc:
            attempts = (int(state['attempts']) if state else 0) + 1
            failed = attempts >= MAX_ATTEMPTS
            backoff = 0.0 if failed else BACKOFF_S[min(attempts - 1, len(BACKOFF_S) - 1)]
            # 补偿写自己也可能失败（磁盘满、库被锁）：它失败只再留一行 warn，
            # 绝不能把异常抛回 turn_end——写失败不得影响其它动作。
            try:
                _write_state(conn, key, session_id,
                             STATUS_FAILED if failed else STATUS_RECORDED,
                             type(exc).__name__, attempts, stamp, stamp,
                             stamp + backoff, len(rows), min(turns), max(turns),
                             _day_of(rows))
                conn.commit()
            except Exception as write_exc:
                logger.warning('[consolidate] 块 %s 连失败记录都写不进去：%s: %s',
                               key[:12], type(write_exc).__name__, str(write_exc)[:120])
            logger.warning('[consolidate] 块 %s 登记失败（第 %d 次，%s）：%s',
                           key[:12], attempts, type(exc).__name__, str(exc)[:120])
            return {'enabled': True, 'pending': len(rows), 'recorded': 0,
                    'failed': failed, 'code': type(exc).__name__,
                    'attempts': attempts, 'block_key': key}
        logger.warning('[consolidate] 块 %s 已登记（%s，%d 行）',
                       key[:12], code, len(rows))
        return {'enabled': True, 'pending': len(rows), 'recorded': 1,
                'code': code, 'block_key': key, 'attempts': 0,
                'day': _day_of(rows), 'first_turn': min(turns), 'last_turn': max(turns)}
    finally:
        conn.close()
