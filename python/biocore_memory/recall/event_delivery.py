# -*- coding: utf-8 -*-
"""干净的事件交付（新路径）：纯函数，不读库、不读环境、不调模型。

输入 entries（id/text/ts/vector）与查询向量；输出交付块。
规则：
  · 事件由确定性切分得到，尾巴单独成件（覆盖率 100%）；
  · 每件分数 = 成员与查询向量余弦的最大值；
  · 取前 preset 件；最高分低于 floor → shown=0 + no_relevant_events（答不上就不给）；
  · 渲染：`【日期-序号】` + 该件全部成员原文（按 ts），不截断、不看字数；
  · 字符预算（budget_chars）只是历史兼容：None = 不设预算，picked 件全部交付，
    超了也超（事件数才是预算），used_chars 照常统计。
"""
from __future__ import annotations
import time

import math

from biocore_memory.event import events as _EV


def _cos(a, b):
    try:
        va, vb = list(a or []), list(b or [])
        if not va or len(va) != len(vb):
            return 0.0
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(y * y for y in vb))
        return dot / (na * nb) if na and nb else 0.0
    except Exception:
        return 0.0


def _day(ts):
    import time as _t
    try:
        return _t.strftime('%Y-%m-%d', _t.localtime(float(ts)))
    except Exception:
        return ''


def _turn_index(rows):
    """轮号 → 行下标，照抄 build_events 的规则。

    build_events 用**轮号**指代行（成员是轮号，不是下标）；轮号重复时它会先把
    1..n 重编一遍。这里必须照抄同一条规则，否则每件都整体错位一行：
    首行永远掉进尾巴、件与件之间还会串行。
    """
    turns = [_EV._turn(row, index) for index, row in enumerate(rows)]
    if len(set(turns)) < len(turns):
        turns = [index + 1 for index in range(len(rows))]
    mapping = {}
    for index, turn in enumerate(turns):
        mapping.setdefault(turn, index)
    return mapping


def _segment(rows):
    """确定性切分；返回 [成员行列表]，尾巴单独成件，覆盖 100%。"""
    if not rows:
        return []
    # 与 build_events 内部同序（ts, 轮号），下标映射才对得上
    ordered = sorted(rows, key=lambda r: (float(r.get('ts') or 0.0), _EV._turn(r, 0)))
    built = _EV.build_events(ordered, llm=None, flags={'event_layer': True}, merge_small=False)
    index_by_turn = _turn_index(ordered)
    out, seen = [], set()
    for ev in built:
        turns = [t for t in (ev.get('members') or []) if isinstance(t, int)]
        members = [ordered[index_by_turn[t]] for t in turns if t in index_by_turn]
        if not members:
            continue
        out.append(members)
        seen.update(str(m.get('id') or '') for m in members)
    # 尾巴：没进任何事件的行走位成件（哪怕只有 1 行）
    tail = [r for r in ordered if str(r.get('id') or '') not in seen]
    if tail:
        out.append(tail)
    return out


def _span(members):
    """首末成员的本地时间跨度：优先用事件上已存的值，否则现算。"""
    for row in members:
        stored = str(row.get('event_span') or '').strip()
        if stored:
            return stored
    try:
        lo = min(float(r.get('ts') or 0.0) for r in members)
        hi = max(float(r.get('ts') or 0.0) for r in members)
        fmt = '%Y-%m-%d %H:%M'
        if lo <= 0.0 and hi <= 0.0:
            return ''
        return time.strftime(fmt, time.localtime(lo)) + ' ~ ' + time.strftime(fmt, time.localtime(hi))
    except Exception:
        return ''


def _event_code(members, index):
    """稳定编码：优先取成员上已存的 event_code，否则按名次 AM%04d（只在本块内唯一）。"""
    for row in members:
        code = str(row.get('event_code') or '').strip()
        if code:
            return code
    return 'AM%04d' % int(index)


TEXT_MODES = ('raw', 'summary', 'auto')
TIER_FULL_DAYS = 7.0
TIER_LINE_DAYS = 90.0


def _tier(age_days):
    if age_days <= TIER_FULL_DAYS:
        return 'raw'
    if age_days <= TIER_LINE_DAYS:
        return 'mixed'
    return 'line'


INDEX_OPEN = '<记忆索引>'
INDEX_CLOSE = '</记忆索引>'
BODY_OPEN = '<记忆正文>'
BODY_CLOSE = '</记忆正文>'

LONG_EVENT_CHARS_DEFAULT = 1200
SEG_MAX_TURNS = 15
SEG_MAX_CHARS = 900


def _segments(members, long_chars=LONG_EVENT_CHARS_DEFAULT):
    """超长事件切成多段（每段 <=15 句且 <=900 字）；不超长就原样一段。"""
    total = sum(len(str(r.get('text') or '')) for r in members)
    if total <= int(long_chars or LONG_EVENT_CHARS_DEFAULT) and len(members) <= SEG_MAX_TURNS:
        return [list(members)]
    out, buf, used = [], [], 0
    for row in members:
        size = len(str(row.get('text') or ''))
        if buf and (len(buf) >= SEG_MAX_TURNS or used + size > SEG_MAX_CHARS):
            out.append(buf)
            buf, used = [], 0
        buf.append(row)
        used += size
    if buf:
        out.append(buf)
    return out


def _render_event(members, index, mode, now, segment_summaries=None,
                  long_chars=LONG_EVENT_CHARS_DEFAULT):
    """按模式渲染一件事。raw=全部原话；summary=只给摘要行；auto=按时间分层。"""
    day = _day(members[0].get('ts'))
    head = '【%s-%d】' % (day, index)
    overview = ''
    for row in members:
        text = str(row.get('overview') or '').strip()
        if text:
            overview = text
            break
    first = str(members[0].get('text') or '').strip()
    segs = _segments(members, long_chars)
    if segment_summaries is None:
        for row in members:
            _seg = row.get('segment_summaries')
            if _seg:
                segment_summaries = _seg
                break
    if len(segs) > 1:
        # 超长事件：整件先给一行标题，再逐段渲染（完整性不丢）
        lines = [head + (overview or first)]
        ids = []
        for k, seg in enumerate(segs, 1):
            seg_head = '【%s-%d-段%d】' % (day, index, k)
            if mode in ('summary', 'auto'):
                text = ''
                if isinstance(segment_summaries, (list, tuple)) and len(segment_summaries) >= k:
                    text = str(segment_summaries[k - 1] or '').strip()
                lines.append(seg_head + (text or str(seg[0].get('text') or '').strip()))
                ids.append(str(seg[0].get('id') or ''))
            else:
                lines.append(seg_head)
                for row in seg:
                    when = _day(row.get('ts'))
                    body = str(row.get('text') or '').strip()
                    lines.append('- %s%s' % (body, ('（%s）' % when) if when else ''))
                    ids.append(str(row.get('id') or ''))
        return lines, ids
    lines = []
    ids = []
    if mode == 'summary':
        # 只有概述行；没有概述就退化成首句，不编造
        lines.append(head + (overview or first))
        return lines, [str(members[0].get('id') or '')]
    # auto 的判据是**每件长度**（不是时间）：<=long_chars 给原文（落到下面），
    # >long_chars 走多段小总结（上方分段分支已处理）。时间分档只由 tiered_delivery_on 负责。
    # （本函数不再看 now 做分档。）
    lines.append(head)
    for row in members:
        when = _day(row.get('ts'))
        text = str(row.get('text') or '').strip()
        lines.append('- %s%s' % (text, ('（%s）' % when) if when else ''))
        ids.append(str(row.get('id') or ''))
    return lines, ids


def deliver_events(entries, query_vector, *, preset=8, floor=0.20, now=None,
                   text_mode='raw', long_chars=LONG_EVENT_CHARS_DEFAULT,
                   budget_chars=None):
    """见模块文档。entries 为空返回 None（由调用方决定降级）。

    budget_chars=None（缺省）表示不设字符预算：picked 件全部交付，used_chars 照常统计。
    只有显式传了正数才做装入判断（历史兼容路径）。
    """
    rows = [r for r in (entries or []) if isinstance(r, dict) and str(r.get('text') or '').strip()]
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: float(r.get('ts') or 0.0))
    groups = _segment(rows)
    scored = []
    for ordinal, members in enumerate(groups, 1):
        best = max((_cos(m.get('vector'), query_vector) for m in members), default=0.0)
        scored.append((best, members, ordinal))
    scored.sort(key=lambda triple: -triple[0])
    best_overall = scored[0][0] if scored else 0.0
    if best_overall < float(floor):
        return {'mode': 'event_rank', 'picked': 0, 'shown': 0, 'ids': [],
                'chars': 0, 'text': '', 'scores': [round(best_overall, 6)],
                'no_relevant_events': True}
    picked = scored[:max(1, int(preset))]
    mode = text_mode if text_mode in TEXT_MODES else 'raw'
    if mode != text_mode:
        # 非法值不抛：warn 一次并回退 raw（纯函数，不读环境）
        try:
            import logging as _lg
            _lg.getLogger(__name__).warning('[event_delivery] 非法 text_mode=%r，回退 raw', text_mode)
        except Exception:
            pass
    lines, ids = [], []
    _budget = None if budget_chars is None else max(0, int(budget_chars or 0))
    _used = 0
    _kept = []
    for index, (score, members, ordinal) in enumerate(picked, 1):
        _lines, _ids = _render_event(members, index, mode, now, long_chars=long_chars)
        _size = sum(len(line) + 1 for line in _lines)
        if _budget and _used + _size > _budget:
            break                      # 显式给了预算才走到这里：整件装不下就停，不截断
        lines.extend(_lines)
        ids.extend(_ids)
        _used += _size
        _kept.append((score, members, ordinal))
    body = chr(10).join(lines)
    index_lines = []
    for offset, (_score, members, ordinal) in enumerate(_kept, 1):
        head_text = ''
        for row in members:
            head_text = str(row.get('overview') or row.get('text') or '').strip()
            if head_text:
                break
        index_lines.append('%s｜%s｜%s' % (_event_code(members, ordinal), _span(members) or '-', head_text[:30]))
    block = (chr(10).join([INDEX_OPEN] + index_lines + [INDEX_CLOSE, BODY_OPEN])
             + chr(10) + body + chr(10) + BODY_CLOSE)
    return {'mode': 'event_rank', 'picked': len(_kept), 'shown': len(_kept),
            'ids': ids, 'chars': len(block), 'text': block,
            'scores': [round(score, 6) for score, _m, _o in _kept],
            'budget_chars': _budget, 'used_chars': _used}
