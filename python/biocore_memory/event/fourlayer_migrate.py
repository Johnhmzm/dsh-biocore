# -*- coding: utf-8 -*-
"""四层迁移执行器：dry-run 先算不写；写入时逐库备份并留 manifest。

只碰 events 表与它自己新增的两张表（events_fourlayer / event_sentences），
不改 events 的既有字段，因此旧读法不受影响。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from . import fourlayer

SCHEMA_VERSION = 'b1-fourlayer-1'
FOURLAYER_DDL = (
    'CREATE TABLE IF NOT EXISTS events_fourlayer ('
    ' event_id TEXT PRIMARY KEY, day TEXT, ordinal INTEGER, thread TEXT,'
    ' fingerprint TEXT, updated_at REAL)',
    'CREATE TABLE IF NOT EXISTS event_sentences ('
    ' event_id TEXT, sentence_index INTEGER, turn INTEGER, ts REAL,'
    ' speaker TEXT, text TEXT, PRIMARY KEY (event_id, sentence_index))',
)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


CANONICAL_FIELDS = ('event_id', 'start', 'end', 'members', 'text', 'overview',
                    'claims', 'day', 'ordinal', 'thread')


def canonical_projection(event, ordinal=None):
    """指纹只看这十个字段：显式排掉 vector / centroid / sentences / tokens 与二进制。

    为什么要投影：同一事件在内存里可能带成员向量、在库里不带，若各算各的指纹，
    写入明明正确也会被判成“内容变了”。投影之后两边算的是同一件事。
    """
    event = event or {}
    out = {}
    for field in CANONICAL_FIELDS:
        value = event.get(field)
        if field == 'ordinal' and value in (None, 0) and ordinal is not None:
            value = ordinal
        out[field] = value
    return out


def fingerprint(mapping):
    """一条映射的内容指纹：先规范化投影，再序列化。"""
    payload = json.dumps(canonical_projection(mapping, ordinal=mapping.get('ordinal')),
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def read_events(db_path):
    """只读事件行；库不存在返回 []。"""
    path = Path(db_path)
    if not path.exists():
        return []
    uri = 'file:%s?mode=ro' % str(path).replace('\\', '/')
    try:
        conn = sqlite3.connect(uri, uri=True)
    except Exception:
        return []
    try:
        out = []
        for payload, centroid in conn.execute('SELECT payload, centroid FROM events ORDER BY start'):
            try:
                event = json.loads(payload)
            except Exception:
                continue
            if isinstance(event, dict):
                out.append(event)
        return out
    except Exception:
        return []
    finally:
        conn.close()


def existing_fourlayer(db_path):
    """已写入的四层行：event_id -> fingerprint；没有表就返回 {}。"""
    path = Path(db_path)
    if not path.exists():
        return {}
    uri = 'file:%s?mode=ro' % str(path).replace('\\', '/')
    try:
        conn = sqlite3.connect(uri, uri=True)
    except Exception:
        return {}
    try:
        return {str(eid): str(fp or '')
                for eid, fp in conn.execute('SELECT event_id, fingerprint FROM events_fourlayer')}
    except Exception:
        return {}
    finally:
        conn.close()


def plan_session(db_path):
    """一个会话库的迁移计划（不写任何东西）。"""
    events = read_events(db_path)
    ordinals = fourlayer.day_ordinals(events)
    written = existing_fourlayer(db_path)
    add = update = unchanged = skipped = tagged = 0
    sentences = 0
    changes = []
    skipped_details = []
    for event in events:
        if str(event.get('day') or '') and int(event.get('ordinal') or 0) > 0:
            tagged += 1          # 已经有 day/ordinal 的事件（正常应为 0）
        event_id = str(event.get('event_id') or '')
        if not event_id:
            # 没有 event_id 的事件无法落四层键：如实计数，不猜、不丢进 add
            skipped += 1
            skipped_details.append({'start': int(event.get('start') or 0),
                                    'end': int(event.get('end') or 0),
                                    'members': len(event.get('members') or []),
                                    'reason': 'no_event_id'})
            continue
        mapping = fourlayer.map_event(event, ordinal=ordinals.get(event_id, 0))
        fp = fingerprint(mapping)
        if event_id not in written:
            add += 1
        elif written[event_id] != fp:
            update += 1
            changes.append(event_id)
        else:
            unchanged += 1
            continue
        sentences += len(mapping['sentences'])
    return {'events': len(events), 'would_add': add, 'would_update': update,
            'unchanged': unchanged, 'skipped_no_id': skipped,
            'skipped_details': skipped_details, 'already_tagged': tagged,
            'sentences_delta': sentences, 'changed_ids': changes}


def copy_session(src_db, dst_root, sid):
    """把源库拷到隔离根（只读源，sqlite 备份 API）；返回源/副本的 sha256。"""
    src_db = Path(src_db)
    dst_root = Path(dst_root)
    target = dst_root / 'sessions' / sid / 'memory.db'
    target.parent.mkdir(parents=True, exist_ok=True)
    src_hash = sha256_file(src_db)
    src = sqlite3.connect('file:%s?mode=ro' % str(src_db).replace('\\', '/'), uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if src_hash != sha256_file(src_db):
        raise RuntimeError('source db changed during copy: %s' % src_db)
    return {'source': str(src_db), 'source_sha256': src_hash,
            'copy': str(target), 'copy_sha256': sha256_file(target),
            'copied_at': time.strftime('%Y-%m-%dT%H:%M:%S')}


def apply_session_with_guards(db_path, backup_dir):
    """写入的硬序：pre_sha → backup → 断言相等 → 写 → 写后 dry-run → post_sha。

    任何一步不成立就抛错、不写库。缺省顺序不是流程仪式：它保证「备份 sha 等于
    写入前源 sha」是数字等式，而不是事后靠功能推断。
    """
    db_path = Path(db_path)
    pre_sha = sha256_file(db_path)
    plan = plan_session(db_path)
    if not plan['would_add'] and not plan['would_update']:
        return {'skipped': True, 'reason': 'nothing_to_do', 'pre_sha': pre_sha,
                'post_sha': pre_sha, 'plan': plan}
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ('%s-%s.db' % (db_path.parent.name,
                                         time.strftime('%Y%m%dT%H%M%S')))
    src = sqlite3.connect('file:%s?mode=ro' % str(db_path).replace('\\', '/'), uri=True)
    try:
        dst = sqlite3.connect(str(backup))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    backup_sha = sha256_file(backup)
    if backup_sha != pre_sha:
        raise RuntimeError('backup sha %s != pre_sha %s' % (backup_sha[:12], pre_sha[:12]))
    result = apply_session(db_path, backup_dir)
    after = plan_session(db_path)
    if after['would_add'] or after['would_update']:
        raise RuntimeError('post-write dry-run not clean: add=%d update=%d'
                           % (after['would_add'], after['would_update']))
    post_sha = sha256_file(db_path)
    return {'skipped': False, 'pre_sha': pre_sha, 'backup': str(backup),
            'backup_sha': backup_sha, 'post_sha': post_sha,
            'written': result['written'], 'plan': plan, 'post_plan': after,
            'backup_bytes': backup.stat().st_size}


def apply_session(db_path, backup_dir):
    """真实写入：先备份，再在事务里 upsert 两张表；返回备份路径与计数。"""
    db_path = Path(db_path)
    plan = plan_session(db_path)
    if not plan['would_add'] and not plan['would_update']:
        # 不静默：说明为什么什么都没写（否则调用方会以为“已经落好了”）
        import logging
        logging.getLogger(__name__).warning(
            '[fourlayer] nothing to write: events=%d add=%d update=%d unchanged=%d',
            plan['events'], plan['would_add'], plan['would_update'], plan['unchanged'])
        return {'backup': None, 'plan': plan, 'written': 0}
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ('%s-%s.db' % (db_path.parent.name,
                                         time.strftime('%Y%m%dT%H%M%S')))
    src = sqlite3.connect('file:%s?mode=ro' % str(db_path).replace('\\', '/'), uri=True)
    try:
        dst = sqlite3.connect(str(backup))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    events = read_events(db_path)
    ordinals = fourlayer.day_ordinals(events)
    conn = sqlite3.connect(str(db_path))
    try:
        for ddl in FOURLAYER_DDL:
            conn.execute(ddl)
        written = 0
        for event in events:
            event_id = str(event.get('event_id') or '')
            if not event_id:
                continue
            mapping = fourlayer.map_event(event, ordinal=ordinals.get(event_id, 0))
            conn.execute('INSERT INTO events_fourlayer'
                         ' (event_id, day, ordinal, thread, fingerprint, updated_at)'
                         ' VALUES (?,?,?,?,?,?)'
                         ' ON CONFLICT(event_id) DO UPDATE SET day=excluded.day,'
                         ' ordinal=excluded.ordinal, thread=excluded.thread,'
                         ' fingerprint=excluded.fingerprint, updated_at=excluded.updated_at',
                         (event_id, mapping['day'], mapping['ordinal'],
                          mapping['thread'], fingerprint(mapping), time.time()))
            conn.execute('DELETE FROM event_sentences WHERE event_id=?', (event_id,))
            conn.executemany('INSERT INTO event_sentences'
                             ' (event_id, sentence_index, turn, ts, speaker, text)'
                             ' VALUES (?,?,?,?,?,?)',
                             [(s['event_id'], s['sentence_index'], s['turn'], s['ts'],
                               s['speaker'], s['text']) for s in mapping['sentences']])
            written += 1
        conn.commit()
        if written != plan['would_add']:
            raise RuntimeError('wrote %d rows but plan said %d'
                               % (written, plan['would_add']))
        return {'backup': str(backup), 'plan': plan, 'written': written}
    finally:
        conn.close()


def session_ids(store_root):
    sessions = Path(store_root) / 'sessions'
    if not sessions.exists():
        return []
    return sorted(p.name for p in sessions.iterdir()
                  if p.is_dir() and (p / 'memory.db').exists())

def logic_sha(db_path):
    """逻辑摘要：schema + 全表行的规范化 JSON（辅助证据，不参与硬序判定）。"""
    path = Path(db_path)
    conn = sqlite3.connect('file:%s?mode=ro' % str(path).replace('\\', '/'), uri=True)
    try:
        chunks = []
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            chunks.append('TABLE %s' % table)
            try:
                rows = conn.execute('SELECT * FROM "%s"' % table).fetchall()
            except Exception:
                continue
            for row in rows:
                chunks.append(json.dumps([str(v) if isinstance(v, bytes) else v for v in row],
                                         ensure_ascii=False, sort_keys=True))
        return hashlib.sha256('\n'.join(chunks).encode('utf-8')).hexdigest()
    finally:
        conn.close()


def byte_backup(db_path, backup_dir):
    """字节级备份（硬序用）：checkpoint → 关连接 → 断言无活跃写者 → copyfile → 复核源 sha。

    返回 dict：pre_sha / backup_sha / logic_sha / path。任何一步不成立都抛错。
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.commit()
    finally:
        conn.close()
    for suffix in ('-wal', '-shm'):
        side = Path(str(db_path) + suffix)
        if side.exists() and side.stat().st_size > 0:
            raise RuntimeError('active writer: %s is not empty' % side.name)
    pre_sha = sha256_file(db_path)
    target = backup_dir / ('%s-%s.db' % (db_path.parent.name,
                                         time.strftime('%Y%m%dT%H%M%S')))
    import shutil
    shutil.copyfile(db_path, target)
    after_sha = sha256_file(db_path)
    if after_sha != pre_sha:
        raise RuntimeError('source changed during backup: %s != %s'
                           % (after_sha[:12], pre_sha[:12]))
    backup_sha = sha256_file(target)
    return {'path': str(target), 'pre_sha': pre_sha, 'backup_sha': backup_sha,
            'logic_sha': logic_sha(db_path), 'size': target.stat().st_size}


def verify_counts(db_path):
    """落表后立刻对账：四层行数必须等于 events 行数。"""
    conn = sqlite3.connect('file:%s?mode=ro' % str(db_path).replace('\\', '/'), uri=True)
    try:
        events = conn.execute('SELECT COUNT(1) FROM events').fetchone()[0]
        try:
            fourlayer_rows = conn.execute('SELECT COUNT(1) FROM events_fourlayer').fetchone()[0]
        except Exception:
            fourlayer_rows = -1
    finally:
        conn.close()
    if events != fourlayer_rows:
        raise RuntimeError('events=%d but events_fourlayer=%d' % (events, fourlayer_rows))
    return {'events': events, 'fourlayer_rows': fourlayer_rows}


def run_guarded(db_path, backup_dir, writer, allow_writes=True, check_plan=True):
    """硬序执行器：守卫通过之后才允许 writer 写库。

    writer(conn 无关) 只负责写；本函数负责 checkpoint、字节备份、断言 pre==backup、
    之后调用 writer，再跑一次 dry-run 与 post_sha。守卫失败时 writer 一次都不会被调用。
    """
    db_path = Path(db_path)
    backup = byte_backup(db_path, backup_dir)
    if backup['pre_sha'] != backup['backup_sha']:
        raise RuntimeError('backup sha != pre_sha')
    if not allow_writes:   # 负向测试用：闸门通过也不写
        return {'wrote': False, 'backup': backup}
    written = writer()
    plan_after = plan_session(db_path)
    # 两段式写入时，第一段只落 events，四层表由第二段补；此时不该拿四层计划做判据
    if check_plan and (plan_after['would_add'] or plan_after['would_update']):
        raise RuntimeError('post-write dry-run not clean')
    return {'wrote': True, 'backup': backup, 'written': written,
            'post_plan': plan_after, 'post_sha': sha256_file(db_path),
            'post_logic_sha': logic_sha(db_path)}
