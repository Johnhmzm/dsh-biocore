# -*- coding: utf-8 -*-
"""修复条目与事件之间的链接。

条目和事件本来是两本账：条目存句子，事件存它的成员快照。
只要条目的 turn 或 event_id 没写上，这两本账就对不上——按事件取值、按轮次取值、
按事件做价值筛选，全部静默失效（今晚就是在这里卡住的）。

这个函数把老数据补回去：轮次从位置推，event_id 用事件里那份成员快照反查。
"""
from __future__ import annotations

from typing import Dict, List


def _index_events(events) -> Dict[str, str]:
    """条目 id → 事件 id，来自每个事件自带的那份成员快照。"""
    out: Dict[str, str] = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get('event_id') or '')
        if not eid:
            continue
        for row in ev.get('entries') or []:
            if isinstance(row, dict) and row.get('id'):
                out[str(row['id'])] = eid
    return out


def repair(entries, events) -> Dict[str, int]:
    """就地修补；返回改了哪几类、各多少条。"""
    report = {'turn': 0, 'event_id': 0, 'total': 0}
    if not isinstance(entries, list):
        return report
    by_entry = _index_events(events)
    for index, row in enumerate(entries, 1):
        if not isinstance(row, dict):
            continue
        report['total'] += 1
        try:
            turn = int(row.get('turn') or 0)
        except Exception:
            turn = 0
        if turn <= 0:
            row['turn'] = index
            report['turn'] += 1
        # 事件快照里的 id 是权威的：旧引用可能已经过期（事件重建会换号），
        # 所以这里覆盖写，而不是只在为空时补。
        if True:
            eid = by_entry.get(str(row.get('id') or ''))
            if eid and str(row.get('event_id') or '') != eid:
                row['event_id'] = eid
                report['event_id'] += 1
    return report


def project_links(entries, events):
    """只读投影：按事件成员快照补齐缺失的 event_id（和缺失的 turn）。

    与 repair() 的区别只有一条：不就地改、不写库。交付路径（dispatch 只读）
    只能用它——老库的行没有 event_id 时，按事件交付会退化成"一行一个事件"，
    并列显示也就挂不上事件；这里补上，交付完就丢。

    Returns: (rows, counts)；rows 是浅拷贝后的新列表，入参一行不动。
    """
    report = {'event_id': 0, 'turn': 0, 'total': 0}
    if not isinstance(entries, list):
        return [], report
    by_entry = _index_events(events)
    rows = []
    for index, row in enumerate(entries, 1):
        if not isinstance(row, dict):
            rows.append(row)
            continue
        report['total'] += 1
        copy = dict(row)
        try:
            turn = int(copy.get('turn') or 0)
        except Exception:
            turn = 0
        if turn <= 0:
            copy['turn'] = index
            report['turn'] += 1
        # 只补空，不覆盖：交付期不判定"谁的号更权威"（那是 repair() 的写路径职责），
        # 有值就有值，错了也不在这里改。
        eid = by_entry.get(str(copy.get('id') or ''))
        if eid and not str(copy.get('event_id') or ''):
            copy['event_id'] = eid
            report['event_id'] += 1
        rows.append(copy)
    return rows, report


def broken_counts(entries, events) -> Dict[str, int]:
    """修复前先量一下：多少条缺轮次、多少条缺事件、多少条对不上事件。"""
    if not isinstance(entries, list):
        return {'no_turn': 0, 'no_event': 0, 'dangling': 0, 'total': 0}
    known = {str(ev.get('event_id') or '') for ev in (events or []) if isinstance(ev, dict)}
    no_turn = no_event = dangling = 0
    for row in entries:
        if not isinstance(row, dict):
            continue
        try:
            turn = int(row.get('turn') or 0)
        except Exception:
            turn = 0
        if turn <= 0:
            no_turn += 1
        eid = str(row.get('event_id') or '')
        if not eid:
            no_event += 1
        elif known and eid not in known:
            dangling += 1
    return {'no_turn': no_turn, 'no_event': no_event, 'dangling': dangling,
            'total': len([r for r in entries if isinstance(r, dict)])}
