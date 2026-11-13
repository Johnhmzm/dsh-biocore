# -*- coding: utf-8 -*-
"""干净的事件交付（新路径）：纯函数，不读库、不读环境、不调模型。

输入 entries（id/text/ts/vector）与查询向量；输出交付块。
规则：
  · 事件由确定性切分得到，尾巴单独成件（覆盖率 100%）；
  · 每件分数 = 成员与查询向量余弦的最大值；
  · 取前 preset 件；最高分低于 floor → shown=0 + no_relevant_events（答不上就不给）；
  · 渲染：`【日期-序号】` + 该件全部成员原文（按 ts），不截断、不看字数。
"""
from __future__ import annotations

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


def _segment(rows):
    """确定性切分；返回 [成员行列表]，尾巴单独成件，覆盖 100%。"""
    if not rows:
        return []
    built = _EV.build_events(rows, llm=None, flags={'event_layer': True}, merge_small=False)
    out, seen = [], set()
    for ev in built:
        turns = [t for t in (ev.get('members') or []) if isinstance(t, int) and 0 <= t < len(rows)]
        members = [rows[t] for t in turns]
        if not members:
            continue
        out.append(members)
        seen.update(str(m.get('id') or '') for m in members)
    # 尾巴：没进任何事件的行走位成件（哪怕只有 1 行）
    tail = [r for r in rows if str(r.get('id') or '') not in seen]
    if tail:
        out.append(tail)
    return out


def deliver_events(entries, query_vector, *, preset=8, floor=0.20, now=None):
    """见模块文档。entries 为空返回 None（由调用方决定降级）。"""
    rows = [r for r in (entries or []) if isinstance(r, dict) and str(r.get('text') or '').strip()]
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: float(r.get('ts') or 0.0))
    groups = _segment(rows)
    scored = []
    for members in groups:
        best = max((_cos(m.get('vector'), query_vector) for m in members), default=0.0)
        scored.append((best, members))
    scored.sort(key=lambda pair: -pair[0])
    best_overall = scored[0][0] if scored else 0.0
    if best_overall < float(floor):
        return {'mode': 'event_rank', 'picked': 0, 'shown': 0, 'ids': [],
                'chars': 0, 'text': '', 'scores': [round(best_overall, 6)],
                'no_relevant_events': True}
    picked = scored[:max(1, int(preset))]
    lines, ids = [], []
    for index, (score, members) in enumerate(picked, 1):
        day = _day(members[0].get('ts'))
        lines.append('【%s-%d】' % (day, index))
        for row in members:
            when = _day(row.get('ts'))
            text = str(row.get('text') or '').strip()
            lines.append('- %s%s' % (text, ('（%s）' % when) if when else ''))
            ids.append(str(row.get('id') or ''))
    body = chr(10).join(lines)
    return {'mode': 'event_rank', 'picked': len(picked), 'shown': len(picked),
            'ids': ids, 'chars': len(body), 'text': body,
            'scores': [round(score, 6) for score, _ in picked]}
