# -*- coding: utf-8 -*-
"""压缩记录的日志侧：从 DSH 会话日志里抽 compaction/summary，幂等补录。

为什么要这一层：session/event 上的 compaction/summary 在 web/desktop 拓扑下会
丢（同一次会话压了 3 次，消息进了库、压缩记录一条没进），但**日志是全的**。
所以「漏了就补」不依赖事件投递：读日志、抽事件、按 compaction_id 幂等入库。

三层分工（正文只有一个真源）：
  compaction_record  收下一条、建表、幂等、时间窗；不改写正文一个字
  compaction_log     读日志 -> payload -> 记录（本模块）；同样不改写
  pipeline.scan_compactions / tools.backfill_compactions 只是触发时机不同，
                     落库走的是同一条路

约束（与 docs/SEVEN-STEPS.md 一致：记忆层不写提示词、不调模型）：
  - 只读日志：不改日志、不改既有行；
  - 多帧追加的 .zstd 必须 read_across_frames=True，否则只读到第一帧；
  - 找不到日志不抛异常：补扫是尽力而为，返回 ok=false 的说明即可。

目录约定（2026-11-14 定死，只有一条）：**runtime 目录是唯一约定**。
  runtime = <store>/sessions/<sanitize(session_id)>/memory.db，带 session- 前缀就保留
            —— record 写它、recall 读它（_compaction_attach 也用同一个 id），
            所以只有写进 runtime 目录的压缩记录才可能被挂回召回。
  裸 id 目录（sessions/<去掉 session- 前缀>/memory.db）是历史遗留：
            tools/backfill_compactions.py 早期回填、以及 2026-11-14 之前的手工
            回填都写在那儿（desktop 3 行、test3082 24 行），而 recall 从来读不到它。
            那批数据已用 compaction_scan 默认模式重写进 runtime 目录，两边内容一致。
            裸目录只做复现用：scan(..., store_session_id=<裸 id>)，新代码不要再往里写。
  为什么不是「一个 id 归一到一个目录」：sanitize_session_id 只替换非法字符、不剥前缀，
  所以 session-<id> 与 <id> 是两个目录。这条约定就是把两者钉成「runtime 唯一」。
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from pathlib import Path

from biocore_memory.event import compaction_record as CR

logger = logging.getLogger(__name__)

LOG_NAME = 'session.v4.jsonl.zstd'
EVENT_TYPE = 'compaction/summary'
SESSION_DIR_PREFIX = 'session-'
# 会话归档根：DSH 把 <workspace>/session-<id>/session.v4.jsonl.zstd 放在这里
SESSIONS_ROOT_ENV = 'BIOCORE_DSH_SESSIONS_DIR'

# 与 pipeline.sanitize_session_id 同一套规则（这里只用它算目录名，不复制语义）
SESSION_MAX_LEN = 80
_UNSAFE = re.compile(r'[^A-Za-z0-9._-]+')


class LogNotFound(Exception):
    """会话日志找不到（或目录里没有 .zstd）。调用方自己决定报错还是 ok=false。"""


def sessions_root(root=None):
    """会话归档根：显式入参 > 环境变量 > ~/.dsh/sessions。"""
    if root:
        return Path(str(root))
    override = str(os.environ.get(SESSIONS_ROOT_ENV) or '').strip()
    if override:
        return Path(override)
    return Path.home() / '.dsh' / 'sessions'


def bare_session_id(session_id):
    """去掉 session- 前缀：日志目录名与历史回填用的都是这个形状。"""
    text = str(session_id or '').strip()
    if text.startswith(SESSION_DIR_PREFIX):
        return text[len(SESSION_DIR_PREFIX):]
    return text


def runtime_session_id(session_id):
    """runtime 那套归一：只 sanitize，前缀保留（与 record / recall 同一目录）。"""
    raw = str(session_id or '').strip()[:SESSION_MAX_LEN]
    raw = _UNSAFE.sub('_', raw).strip('._-')
    return raw or 'default'


def log_dir_name(session_id):
    """session-<裸 id>：日志目录名，永远带前缀。"""
    return SESSION_DIR_PREFIX + bare_session_id(session_id)


def log_path(target):
    """--session 收文件或目录；目录里找 session.v4.jsonl.zstd（也认任意 .zstd）。"""
    path = Path(str(target))
    if path.is_dir():
        direct = path / LOG_NAME
        if direct.exists():
            return direct
        found = sorted(path.glob('*.zstd'))
        if found:
            return found[0]
        raise LogNotFound('目录里没有 .zstd 会话日志：%s' % path)
    if not path.exists():
        raise LogNotFound('会话日志不存在：%s' % path)
    return path


def session_id_of(path):
    """session-<id> 的目录名 -> <id>；认不出来就退到目录名本身。"""
    name = Path(path).resolve().parent.name
    if name.startswith(SESSION_DIR_PREFIX):
        return name[len(SESSION_DIR_PREFIX):]
    return name


def iter_events(path):
    """流式逐行产出事件；多帧压缩必须 read_across_frames=True。"""
    import zstandard as zstd
    decompressor = zstd.ZstdDecompressor()
    with open(str(path), 'rb') as raw:
        with decompressor.stream_reader(raw, read_across_frames=True) as reader:
            with io.TextIOWrapper(reader, encoding='utf-8') as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(event, dict):
                        yield event


def compaction_id_of(event):
    """只取 id（不拼正文）：补扫用它判断"这条记过没有"，记过就不重建。"""
    data = event.get('data') if isinstance(event.get('data'), dict) else {}
    return str(data.get('compactionId') or '').strip()


def payload_of(event):
    """compaction/summary -> compactions 的一行（字段映射只在这里定义一次）。"""
    data = event.get('data') if isinstance(event.get('data'), dict) else {}
    return {
        'compaction_id': compaction_id_of(event),
        'range_start': (data.get('shadowedRange') or {}).get('start'),
        'range_end': (data.get('shadowedRange') or {}).get('end'),
        'shadowed_count': len(data.get('shadowedSeqs') or []),
        'shadowed_tokens': data.get('shadowedTokenCount'),
        'provider': data.get('provider'),
        'model': data.get('model'),
        'usage': data.get('usage'),
        'text': CR.summary_text(data.get('summary')),
        'covered_at': event.get('time'),
    }


def backfill(target, store, session_id=None, now=None):
    """读一份会话日志，幂等补录进 store。不打印、不调模型、不改正文。

    返回 {session, log, db, seen, written, skipped, reasons, warnings, last}：
      seen    日志里 compaction/summary 的条数
      written 这次真正新增的行数（幂等：第二次跑是 0）
      skipped 没写进去的条数（重复 / 没有 compaction_id）
      reasons {reason: 条数}
      warnings [(compaction_id, reason)] 只放非 duplicate 的跳过（工具用它打 WARN）
      last    最后写入的那一行的入参（工具用它打一行摘要）

    session_id 省略时按目录名取**裸 id**（工具就是这么调用的，保持它原来的行为）；
    插件侧的 scan 一律显式传 runtime id——裸 id 目录是历史遗留，见模块头。
    """
    path = log_path(target)
    sid = runtime_session_id(session_id if session_id is not None else session_id_of(path))
    db = CR.db_path(store, sid)
    # 只扫"没记录过"的：日志每次都得整份读（多帧 zstd 没法从中间 seek），但已记过的
    # 那些不该再重建正文、更不该再发一条注定被 UNIQUE 挡掉的 INSERT。计数口径不变：
    # 已记过的仍算在 skipped/duplicate 里，所以"第二次跑是 0 写入 + N skipped"照旧。
    known = CR.known_ids(db, sid)
    written = skipped = seen = known_hits = 0
    reasons = {}
    warnings = []
    last = None
    for event in iter_events(path):
        if event.get('type') != EVENT_TYPE:
            continue
        seen += 1
        cid = compaction_id_of(event)
        if cid and cid in known:
            skipped += 1
            known_hits += 1
            reasons['duplicate'] = reasons.get('duplicate', 0) + 1
            continue
        row = payload_of(event)
        out = CR.record(db, sid, row, now=now if now is not None else time.time())
        if out['recorded']:
            written += 1
            last = row
            known.add(cid)
            continue
        skipped += 1
        reason = str(out.get('reason') or 'skipped')
        reasons[reason] = reasons.get(reason, 0) + 1
        if reason != 'duplicate':
            warnings.append((row.get('compaction_id') or '', reason))
    return {'session': sid, 'log': str(path), 'db': str(db), 'seen': seen,
            'written': written, 'skipped': skipped, 'reasons': reasons,
            'warnings': warnings, 'last': last,
            # 这次读日志时"已经记过、所以连正文都没重建"的条数（可观测，不参与写入）
            'known': known_hits}


def find_log(session_id, root=None):
    """在 <sessions>/*/session-<id>/session.v4.jsonl.zstd 里找这个会话的日志。

    同一个 id 可能落在多个 workspace 目录下（换过 cwd 就会），取 mtime 最新的
    那份：正在跑的那次会话总在写它。
    """
    base = sessions_root(root)
    name = log_dir_name(session_id)
    try:
        hits = [path for path in base.glob('*/%s/%s' % (name, LOG_NAME))
                if path.is_file()]
    except Exception as exc:
        logger.debug('[compaction] 日志搜索失败：%s: %s', type(exc).__name__, str(exc)[:120])
        return None
    if not hits:
        return None

    def _mtime(path):
        try:
            return path.stat().st_mtime
        except Exception:
            return 0.0

    hits.sort(key=_mtime)
    return hits[-1]


def scan(session_id, store, root=None, store_session_id_override=None, now=None):
    """补扫一个会话：定位日志 -> 抽 compaction/summary -> 幂等补录缺失行。

    返回 {ok, scanned, inserted, skipped, log}（外加 session_id/db/reason）：
      ok=false 只表示「这一趟没补成」（找不到日志 / 没有 store），不是异常。

    落库目录走模块头那条唯一约定（runtime 目录）。store_session_id_override 只用来
    复现历史遗留的裸 id 目录，正常调用不要传。
    """
    raw = str(session_id or '').strip()
    if not raw:
        return {'ok': False, 'reason': 'no_session', 'scanned': 0, 'inserted': 0,
                'skipped': 0, 'log': '', 'session_id': '', 'db': ''}
    sid = runtime_session_id(store_session_id_override or raw)
    path = find_log(raw, root)
    if path is None:
        return {'ok': False, 'reason': 'log_not_found', 'scanned': 0, 'inserted': 0,
                'skipped': 0, 'log': '', 'session_id': sid, 'db': '',
                'detail': '没有找到会话日志 %s/%s' % (log_dir_name(raw), LOG_NAME)}
    out = backfill(path, store, session_id=sid, now=now)
    return {'ok': True, 'scanned': out['seen'], 'inserted': out['written'],
            'skipped': out['skipped'], 'log': out['log'], 'session_id': sid,
            'db': out['db'], 'reasons': out['reasons'], 'known': out['known']}
