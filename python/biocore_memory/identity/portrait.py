# -*- coding: utf-8 -*-
"""长：画像的结构与上限。内容由模型决定，这里只管形状。

口径（2026-11-13 用户定稿）：
  - **只增不减**：没有 delete、没有 retired。旧印象不删，靠交付时取舍。
  - 两条动作：`add` / `update`（改写）。改写要能回滚，所以每次改动留快照。
  - 每条必须挂证据事件键（`evidence` 至少一个非空字符串）。
  - `MAX_ITEMS = 24` 是**库内上限**（防无限膨胀），不是交付上限。
  - 交付时只取最近更新的 N 条（`select_for_delivery`, N 缺省 8、可配），
    同一主题的两种说法都给、各带日期、新的在前——那是交付层的事，不是删数据。
内容（写什么话）留空：`text` 允许空串，等总结层填；这里不判断、不概括、不调模型。
"""
from __future__ import annotations

import copy

SCHEMA_VERSION = 1
MAX_ITEMS = 24
MAX_HISTORY = 32
ACTIONS = ('add', 'update')
DELIVERY_TOP_DEFAULT = 8
DELIVERY_TOP_MAX = 50


def empty():
    return {'schemaVersion': SCHEMA_VERSION, 'revision': 0, 'items': [], 'history': []}


def _evidence(value):
    """证据事件键：只收非空字符串，去重保序；不猜、不补。"""
    out = []
    for item in value or []:
        text = str(item or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def validate_op(op):
    """形状校验：返回 (ok, reason)。不改任何东西。"""
    if not isinstance(op, dict):
        return False, 'not_an_object'
    action = str(op.get('action') or '').strip().lower()
    if action not in ACTIONS:
        # delete 不是被遗忘的动作，而是不存在的动作：画像只增不减
        return False, 'bad_action'
    if not _evidence(op.get('evidence')):
        return False, 'no_evidence'
    if action == 'update' and not str(op.get('id') or '').strip():
        return False, 'missing_id'
    return True, ''


def _next_id(items):
    used = {str(item.get('id') or '') for item in items}
    index = 1
    while 'P%02d' % index in used:
        index += 1
    return 'P%02d' % index


def apply_ops(portrait, ops, now=None):
    """按顺序应用 add/update；逐条校验，坏动作拒收并给理由，不抛异常。"""
    state = copy.deepcopy(portrait if isinstance(portrait, dict) else empty())
    state.setdefault('items', [])
    state.setdefault('history', [])
    state.setdefault('revision', 0)
    stamp = float(now if now is not None else 0.0)
    applied, rejected = [], []
    for op in ops or []:
        ok, reason = validate_op(op)
        if not ok:
            rejected.append({'op': op if isinstance(op, dict) else {}, 'reason': reason})
            continue
        action = str(op.get('action')).strip().lower()
        evidence = _evidence(op.get('evidence'))
        text = str(op.get('text') or '')          # 允许空串：内容由模型决定
        snapshot = copy.deepcopy(state['items'])
        if action == 'add':
            if len(state['items']) >= MAX_ITEMS:
                rejected.append({'op': op, 'reason': 'cap'})
                continue
            item = {'id': _next_id(state['items']), 'text': text,
                    'evidence': evidence, 'revision': state['revision'] + 1,
                    'updated_at': stamp}
            state['items'].append(item)
            applied.append({'action': 'add', 'id': item['id']})
        else:                                     # update
            target = next((it for it in state['items']
                           if str(it.get('id')) == str(op.get('id')).strip()), None)
            if target is None:
                rejected.append({'op': op, 'reason': 'unknown_id'})
                continue
            target['text'] = text
            target['evidence'] = evidence
            target['revision'] = state['revision'] + 1
            target['updated_at'] = stamp
            applied.append({'action': 'update', 'id': target['id']})
        state['revision'] = int(state['revision']) + 1
        state['history'].append({'revision': state['revision'],
                                 'action': action,
                                 'id': applied[-1]['id'],
                                 'before': snapshot,
                                 'items': copy.deepcopy(state['items'])})
        if len(state['history']) > MAX_HISTORY:
            state['history'] = state['history'][-MAX_HISTORY:]
    return {'portrait': state, 'applied': applied, 'rejected': rejected,
            'revision': state['revision']}


def rollback(portrait, revision):
    """回到某一版：用该版快照复原条目；找不到该版返回 ok=False。"""
    state = copy.deepcopy(portrait if isinstance(portrait, dict) else empty())
    try:
        target = int(revision)
    except Exception:
        return {'portrait': state, 'ok': False, 'reason': 'bad_revision'}
    entry = next((row for row in reversed(state.get('history') or [])
                  if int(row.get('revision') or 0) == target), None)
    if entry is None:
        return {'portrait': state, 'ok': False, 'reason': 'unknown_revision'}
    state['items'] = copy.deepcopy(entry.get('items') or [])
    state['revision'] = target
    return {'portrait': state, 'ok': True, 'revision': target}


def resolve_top(payload=None, env=None):
    """交付条数 N：config(recall.portrait_top) > env > 8，夹到 [1, 50]。"""
    from biocore_memory.config import recall_params as _rp
    try:
        return int(_rp.resolve('portrait_top', payload, env))
    except Exception:
        return DELIVERY_TOP_DEFAULT


def select_for_delivery(portrait, limit=None, payload=None, env=None):
    """交付时只取最近更新的 N 条：库里全留，注入只带这一段。

    排序依据是 `updated_at`（同等则 revision 大的在前）；不判断新旧对错，
    同一主题的两条都会留在这一段里，各自带日期出去。
    """
    items = list((portrait or {}).get('items') or [])
    count = int(limit) if limit is not None else resolve_top(payload, env)
    count = max(1, min(DELIVERY_TOP_MAX, count))
    ordered = sorted(items, key=lambda item: (-float(item.get('updated_at') or 0.0),
                                             -int(item.get('revision') or 0)))
    return ordered[:count]
