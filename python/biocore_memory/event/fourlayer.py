# -*- coding: utf-8 -*-
"""四层结构映射：事件 → (day, ordinal, sentences)。纯函数，零 IO。

这一层只做推导，不碰库、不碰路径：给它一个事件 dict，它回答四件事——
这件事从哪天开始（day）、迁移时该排第几（ordinal）、它的句子怎么落（sentences）、
以及哪些字段必须留空待总结（thread）。

两条不同的调用路径故意分开：
  day_ordinals(events)   迁移期：同一天按 (start, event_id) 重新排序编号
  next_ordinal(existing) 运行期：只追加，取该日 max+1，永不重排、永不回收
把它们混进一个函数，以后补录历史事件时就会静默改掉别人的号——那等于改标签引用。
"""
from __future__ import annotations

import datetime as _dt

DAY_UNKNOWN = ''
THREAD_PENDING = ''


def _local_day(ts):
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d')
    except Exception:
        return DAY_UNKNOWN


def backfill_turns(entries):
    """给缺 turn 的条目补位：有 turn 的照用，缺的按 ts 排序后取“未被占用的位置”。

    幂等：同一批输入连跑两次结果相同（补出来的 turn 再跑一次就被当成“已有 turn”）；
    补出的号避开该批已经出现的 turn，因此不会和真实轮次撞车。
    返回 (entries_with_turn, filled_count)，不修改入参。
    """
    rows = [dict(row) if isinstance(row, dict) else {} for row in (entries or [])]
    used = set()
    for row in rows:
        try:
            turn = int(row.get('turn') or 0)
        except Exception:
            turn = 0
        if turn > 0:
            used.add(turn)
    missing = []
    for index, row in enumerate(rows):
        try:
            turn = int(row.get('turn') or 0)
        except Exception:
            turn = 0
        if turn <= 0:
            missing.append((float(row.get('ts') or 0.0), index))
    missing.sort()
    filled = 0
    cursor = 1
    for _ts, index in missing:
        while cursor in used:
            cursor += 1
        rows[index]['turn'] = cursor
        used.add(cursor)
        cursor += 1
        filled += 1
    return rows, filled


def event_day(event):
    """这件事从哪天开始：事件内最早一条 entry 的本地日期；没有 ts 返回空串。

    取最早而不是最晚：day + ordinal 读作“这件事从哪天开始、那天第几件”，
    与迁移期按 start 排序的口径一致。
    """
    stamps = []
    for row in (event or {}).get('entries') or []:
        try:
            stamps.append(float(row.get('ts') or 0.0))
        except Exception:
            continue
    stamps = [value for value in stamps if value > 0]
    if not stamps:
        return DAY_UNKNOWN
    return _local_day(min(stamps))


def day_ordinals(events):
    """迁移期编号：按 day 分组，组内按 (start, event_id) 排序，1 起。

    start 相同的事件用 event_id 兜底，保证同一份库每次跑出同样的号。
    """
    order = []
    for event in events or []:
        order.append((event_day(event),
                      int((event or {}).get('start') or 0),
                      str((event or {}).get('event_id') or ''),
                      event))
    order.sort(key=lambda item: (item[0], item[1], item[2]))
    out = {}
    counters = {}
    for day, _start, event_id, _event in order:
        counters[day] = counters.get(day, 0) + 1
        out[event_id] = counters[day]
    return out


def next_ordinal(existing_ordinals):
    """运行期追加：该日现有 max + 1；空集合从 1 开始。

    只追加、不重排、不回收：补录一条更早的事件也照样拿 max+1，因为已经发出的
    标签引用不能因为补录而改号。
    """
    values = [int(value) for value in (existing_ordinals or [])
              if isinstance(value, int) or (isinstance(value, float) and value.is_integer())]
    return (max(values) + 1) if values else 1


def event_sentences(event):
    """句子层：把事件成员按原顺序落成句子行，文本一字不改。

    index 从 0 起；turn/ts/speaker/text 缺省为空，不猜。
    """
    out = []
    rows = (event or {}).get('entries') or []
    for index, row in enumerate(rows):
        row = row if isinstance(row, dict) else {}
        out.append({'event_id': str((event or {}).get('event_id') or ''),
                    'sentence_index': index,
                    'turn': int(row.get('turn') or 0),
                    'ts': float(row.get('ts') or 0.0),
                    'speaker': str(row.get('role') or row.get('speaker') or ''),
                    'text': str(row.get('text') or '')})
    return out


def map_event(event, ordinal=None):
    """单条事件的四层映射结果；ordinal 由调用方给（迁移用 day_ordinals，运行期用 next_ordinal）。

    thread 一律留空待总结：迁移不猜线索名，也不把空值写成 None（旧代码会做 bool 判断）。
    """
    day = event_day(event)
    return {'event_id': str((event or {}).get('event_id') or ''),
            'day': day,
            'ordinal': int(ordinal or 0),
            'thread': THREAD_PENDING,
            'start': int((event or {}).get('start') or 0),
            'end': int((event or {}).get('end') or 0),
            'members': list((event or {}).get('members') or []),
            'overview': str((event or {}).get('overview') or ''),
            'sentences': event_sentences(event)}
