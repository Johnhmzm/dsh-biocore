# -*- coding: utf-8 -*-
"""Delivery rendering: turn a ranked selection into the injected block.

This module owns what the block *looks like*: one line per row, whole lines only
(a line that does not fit is skipped, never clipped -- raw text stays whole), and
for event delivery one whole event at a time, best-ranked first, stopping when the
budget is spent.

It owns no selection: ranking, rerank, echo filtering and sampling stay in
biocore_runtime.pipeline. Every switch that changes rendering (memory codes,
atomic blocks, subnotes, event top, rank mode) arrives as an argument, so this
module never imports the runtime and cannot drift with it.
"""
from __future__ import annotations

import logging

# 本模块会打 warn（交付块过长等）：logger 必须在这里定义。
# 缺失时事件渲染每次调用都抛 NameError，被上层捕获后整块退回降级——踩过。
logger = logging.getLogger(__name__)

CHR = chr(10)
HEADER = '【相关记忆】'


def default_label(event):
    """事件标签：优先四层双键（日期 + 当日序号），缺失时回落区间。

    双键让“同一件事只出一次”按事去重，而不是按临时区间：两个事件若属于同一天
    的同一序号，它们就是同一件事。任一字段缺失（空串 / 0）就回落到 <start>-<end>，
    保证老数据（没有 day/ordinal 的事件）行为不变。
    """
    event = event or {}
    day = str(event.get('day') or '')
    try:
        ordinal = int(event.get('ordinal') or 0)
    except Exception:
        ordinal = 0
    if day and ordinal > 0:
        return '%s-%02d' % (day, ordinal)
    return '%s-%s' % (int(event.get('start') or 0), int(event.get('end') or 0))


def dedupe_picked(picked_events, events, label_of=None):
    """同一标签只保留第一次出现的那个事件；顺序不变，正文不动。

    交付层去重：重复标签会把同一段原文往块里塞两遍，既占预算也不提供新信息。
    """
    label_of = label_of or default_label
    labels = {}
    for ev in events or []:
        eid = str(ev.get('event_id') or '')
        if eid:
            labels[eid] = label_of(ev)
    seen, out = set(), []
    for key in picked_events or []:
        label = labels.get(str(key))
        if label is not None:
            if label in seen:
                continue
            seen.add(label)
        out.append(key)
    return out


TIER_FULL_DAYS_ACU = 7.0
TIER_MIXED_DAYS_ACU = 90.0


def tier_of(ts, now=None):
    """时间分层：<=7 天给原文；7–90 天给概述 + 命中原句；>90 天只给一句概括。

    只影响"每件事渲染多少"，**不改排序**：入选顺序仍由调用方按分数给定。
    """
    try:
        stamp = float(ts or 0.0)
        current = float(now if now is not None else 0.0)
    except Exception:
        return 'full'
    if stamp <= 0.0 or current <= 0.0:
        return 'full'
    age_days = max(0.0, (current - stamp) / 86400.0)
    if age_days <= TIER_FULL_DAYS_ACU:
        return 'full'
    if age_days <= TIER_MIXED_DAYS_ACU:
        return 'mixed'
    return 'line'


def _first_sentence(text, limit=40):
    """没有概述时的退化：截首句，不加省略号（原文永不被改写，这里只是渲染）。"""
    body = str(text or '').strip().replace(chr(10), ' ')
    if not body:
        return ''
    for stop in ('。', '！', '？', '.', '!', '?'):
        cut = body.find(stop)
        if 0 <= cut < limit:
            return body[:cut + 1]
    return body[:limit]


def tiered_event_lines(event, members, day_str, tier):
    """按分层给一件事的行：full=全部原文，mixed=概述+命中原句，line=一句概括。"""
    rows = sorted(members, key=lambda item: float(item[1].get('ts') or 0.0))
    if tier == 'full' or not rows:
        out = []
        for _score, row in rows:
            when = day_str(float(row.get('ts') or 0.0)) if row.get('ts') else ''
            out.append('- %s%s' % (str(row.get('text') or '').strip(),
                                   ('（%s）' % when) if when else ''))
        return out
    overview = str((event or {}).get('overview') or '').strip()
    if tier == 'mixed':
        top = max(rows, key=lambda item: float(item[0] or 0.0))
        when = day_str(float(top[1].get('ts') or 0.0)) if top[1].get('ts') else ''
        head = overview or _first_sentence(top[1].get('text'))
        line = '- %s：%s%s' % (head, str(top[1].get('text') or '').strip(),
                               ('（%s）' % when) if when else '')
        return [line]
    line = overview or _first_sentence(rows[0][1].get('text'))
    when = day_str(float(rows[0][1].get('ts') or 0.0)) if rows[0][1].get('ts') else ''
    return ['- %s%s' % (line, ('（%s）' % when) if when else '')]


def _fourlayer_tuple(event):
    """(day, ordinal, start, end) 的四层键信息；缺字段一律回落 0/空串。"""
    event = event or {}
    day = str(event.get('day') or '').strip()
    try:
        ordinal = int(event.get('ordinal') or 0)
    except Exception:
        ordinal = 0
    try:
        start = int(event.get('start') or 0)
    except Exception:
        start = 0
    try:
        end = int(event.get('end') or 0)
    except Exception:
        end = 0
    return day, ordinal, start, end


def stabilize_fourlayer_order(picked, events):
    """按天分块重排：**交付路径不调用**（2026-11-13 决定，见下方理由）。

    交付顺序的唯一依据是相关度。按天把事件凑到一起属于面板导航，不属于交付：
    实测它会让紧预算下的交付集合变化 91.5%，而没有任何数据证明这样更好，
    所以这个函数留在库里备查/备用，render_events 不调用它。

    若将来真要用，语义是：同一天的碎片收拢到该天最好名次的位置，天内按
    ordinal 升序；只调整相邻性，不动分数与名次先后：
      - 某天第一次出现的位置就是这一天整块的位置；
      - 天内按 (ordinal, start, end) 升序；缺 ordinal 的碎片排在本天末尾、
        按区间起点升序（“缺键回落区间”）；
      - 没有任何 day 的条目（老数据）保持原位，顺序与旧版逐字一致。
    """
    info = {}
    for event in events or []:
        eid = str(event.get('event_id') or '')
        if eid:
            info[eid] = _fourlayer_tuple(event)
    blocks = {}
    for key in picked or []:
        day = info.get(str(key), ('', 0, 0, 0))[0]
        if day:
            blocks.setdefault(day, []).append(key)
    if not blocks:
        return list(picked or [])
    big = float('inf')
    emitted = set()
    out = []
    for key in picked or []:
        day, ordinal, start, end = info.get(str(key), ('', 0, 0, 0))
        if not day:
            out.append(key)
            continue
        if day in emitted:
            continue
        emitted.add(day)
        member = blocks[day]
        out.extend(sorted(member, key=lambda item: (
            info[item][1] if info[item][1] > 0 else big,
            info[item][2], info[item][3])))
    return out


def _empty_block(mode, kept, folded=None):
    """A delivery with no body line: empty text, no ids, counts kept for the caller."""
    folded = list(folded or [])
    return {'text': '', 'chars': 0, 'mode': mode,
            'events_picked': 0, 'events_shown': 0, 'kept': kept,
            'shown': 0, 'folded': len(folded), 'folded_ids': folded,
            'folded_events': [], 'events': [], 'ids': [], 'hits': [],
            'empty_delivery': True}


def line_of(row, day_str):
    """One delivered line: "- <text>（<day>）"; the day is omitted when unknown."""
    when = day_str(float(row.get('ts') or 0.0)) if row.get('ts') else ''
    body = str(row.get('text') or '').strip()
    return '- %s%s' % (body, ('（%s）' % when) if when else '')


def expand_window(rows, pool, window=1):
    """每条命中行带上它在池里的前后各 window 条邻居；顺序按池位置，不跨池。

    只决定“带哪几条”，不改选中顺序之外的任何东西：事件排序、event_preset 切片
    都在上层，跟这里无关。window<=0 时原样返回。
    """
    if window is None or window <= 0 or not rows:
        return list(rows)
    order = {}
    for index, row in enumerate(pool or []):
        order[str(row.get("id") or "")] = index
    wanted = set()
    for row in rows:
        centre = order.get(str(row.get("id") or ""))
        if centre is None:
            continue
        for offset in range(-window, window + 1):
            if 0 <= centre + offset < len(pool):
                wanted.add(centre + offset)
    if not wanted:
        return list(rows)
    picked = [pool[index] for index in sorted(wanted)]
    # 池里没有的行（例如扩展召回带进来的）保持原位跟在后面
    known = set(str(row.get("id") or "") for row in picked)
    for row in rows:
        if str(row.get("id") or "") not in known:
            picked.append(row)
    return picked


def render_flat(rows, budget, day_str, kept=0, header=HEADER,
                memory_codes=False, atomic_blocks=True, pool=None, window=0):
    """Flat sentence delivery: every ranked row is a line, packed until full.

    A line that does not fit is skipped and counted as folded; it is never cut,
    because half a sentence reads as a different fact. If the body still does not
    fit (one line longer than the whole budget) and atomic blocks are on, the
    block is withheld entirely rather than delivered short.
    """
    limit = max(50, int(budget) - 90)
    used = len(header)
    out_lines, skipped, shown_ids = [], [], []
    # 命中行 = 打分选中的那些；窗口只把它们变成“交付行”，写回不能算邻居
    hits = [str(row.get("id") or "") for row in rows or []]
    rows = expand_window(rows, pool if pool is not None else rows, window)
    for row in rows or []:
        body_text = str(row.get('text') or '').strip()
        tag = ''
        if memory_codes:
            code = str(row.get('code') or '').strip()
            if code:
                tag = '[%s] ' % code
        when = day_str(float(row.get('ts') or 0.0)) if row.get('ts') else ''
        line = '- %s%s%s' % (tag, body_text, ('（%s）' % when) if when else '')
        if used + len(line) + 1 > limit:
            skipped.append(str(row.get('id') or ''))
            continue
        out_lines.append(line)
        shown_ids.append(str(row.get('id') or ''))
        used += len(line) + 1
    if not out_lines and not shown_ids:
        # 一条正文都没装下时不给壳：header + “已折叠 N 条”对模型只是一句噪声，
        # 按“只看事件、原文不裁”的口径，零行正文不算一次交付。
        return _empty_block('sentence', kept, skipped)
    body = header + CHR + CHR.join(out_lines)
    if skipped:
        body += CHR + '已折叠 %d 条' % len(skipped)
    while len(body) > budget and shown_ids:
        shown_ids.pop()
        out_lines.pop()
        skipped.append('trimmed')
        body = header + CHR + CHR.join(out_lines)
        body += CHR + '已折叠 %d 条' % len(skipped)
    if atomic_blocks and len(body) > budget:
        # 原子门禁：宁可整块不注入，也不给半块
        return {'text': '', 'chars': 0, 'mode': 'sentence',
                'atomic_rejected': True, 'reason': 'over_budget',
                'over_by': len(body) - budget,
                'events_picked': 0, 'events_shown': 0, 'kept': kept,
                'shown': 0, 'folded': 0, 'folded_ids': [], 'folded_events': [],
                'events': [], 'ids': []}
    return {'text': body, 'chars': len(body), 'mode': 'sentence',
            'events_picked': 0, 'events_shown': 0, 'kept': kept,
            'shown': len(shown_ids), 'folded': len(skipped),
            'folded_ids': skipped, 'folded_events': [], 'events': [],
            'ids': shown_ids, 'hits': [i for i in hits if i in set(shown_ids)]}


def _compress_event(members, subs, used_now, limit_now, day_str):
    """Delivery for a long event: the hit lines verbatim plus EVERY subnote.

    Returns None when even that does not fit -- the caller then folds the event
    instead of delivering a summary in place of the原文.
    """
    if not subs:
        return None
    by_score = sorted(members, key=lambda item: -float(item[0]))
    hits = by_score[:3]
    hit_ids = set(str(item[1].get('id') or '') for item in hits)
    rendered = [line_of(item[1], day_str) for item in sorted(
        hits, key=lambda item: float(item[1].get('ts') or 0.0))]
    delta = sum(len(line) + 1 for line in rendered)
    for group in subs:
        note = str(group.get('note') or '').strip()
        if not note:
            continue
        ids = [str(x) for x in (group.get('sentence_ids') or [])]
        line = '  · %s（%d 句，id 见 meta）' % (note, len(ids))
        rendered.append(line)
        delta += len(line) + 1
    if used_now + delta > limit_now:
        return None
    compressed_ids = [str(item[1].get('id') or '') for item in members
                      if str(item[1].get('id') or '') not in hit_ids]
    return {'lines': rendered, 'used': delta, 'compressed_ids': compressed_ids}


# 事件交付不设字数上限：只按事件数控制（取前 N 件），每件给全部原文。
DELIVERY_WARN_CHARS = 12000      # 超过这个长度只 warn（建议调小事件数），不裁剪


CLAIMS_PER_EVENT = 2


def event_claim_lines(event, day_str=None, limit=CLAIMS_PER_EVENT):
    """事件里的说法（claims）渲染：最多 limit 条，新的在前，新旧并列。

    格式：`（说法）<键>：<新值>（日期），<旧值>（日期）`；没有旧值就只给一条。
    没有 claims 返回空列表 —— 不占位、不出现这一段。
    """
    raw = (event or {}).get('claims')
    if not raw:
        return []
    items = []
    if isinstance(raw, dict):
        for key, slot in raw.items():
            rows = (slot or {}).get('values') if isinstance(slot, dict) else slot
            items.append((str(key), list(rows or [])))
    elif isinstance(raw, list):
        # 同一个键的多条说法要并成一行（新的在前），不能每个 dict 各占一行
        grouped = {}
        for entry in raw:
            if isinstance(entry, dict) and entry.get('key'):
                grouped.setdefault(str(entry.get('key')), []).append(entry)
        items.extend(grouped.items())
    lines = []
    for key, rows in items:
        dicts = [row for row in rows if isinstance(row, dict)]
        plain = [row for row in rows if not isinstance(row, dict)]
        if len(dicts) > 1 and all(str(row.get('date') or '') for row in dicts):
            # 有日期就按日期排：新的在前；没有日期时按给出的顺序（后出现的更新）
            dicts = sorted(dicts, key=lambda row: str(row.get('date') or ''))
        newer = dicts[-1] if dicts else None
        older = dicts[-2] if len(dicts) > 1 else None
        parts = []
        if newer is not None:
            parts.append('%s%s' % (str(newer.get('value') or ''),
                                   ('（%s）' % newer.get('date')) if newer.get('date') else ''))
        if older is not None and str(older.get('value') or '') != str((newer or {}).get('value') or ''):
            parts.append('%s%s' % (str(older.get('value') or ''),
                                   ('（%s）' % older.get('date')) if older.get('date') else ''))
        for value in plain:
            parts.append(str(value))
        if not parts:
            continue
        lines.append('（说法）%s：%s' % (key, '，'.join(parts)))
        if len(lines) >= limit:
            break
    return lines


def event_block_lines(event, members, key, day_str):
    """一个事件的渲染：头行 + 正文行（命中句前后各 window 条）。

    头行：'[标签] 概述'，没有概述就用该事件第一句原文（不编造）。
    正文行：命中句及其前后各 window 条；事件里没有命中句时给前 3 条成员。
    不设上限：每件给全部原文，**绝不截断句子、也不丢件**。
    """
    if not members:
        return []
    ordered = sorted(members, key=lambda item: float(item[1].get('ts') or 0.0))
    event = event or {}
    label = str(event.get('label') or key or '').strip()
    # 概述有就附在标签后面，没有就不附（正文永远是全部成员，不靠概述兜底）
    head_text = str(event.get('overview') or '').strip()
    lines = []
    if label or head_text:  # 头行只是标签
        # 头行只是标签：概述有就附后面，没有就不附；正文不受它影响
        head = '【%s】' % label if label else ''
        lines.append(head + head_text if head_text else head)
    body = []
    for _score, row in ordered:
        when = day_str(float(row.get('ts') or 0.0)) if row.get('ts') else ''
        body_text = str(row.get('text') or '').strip()
        line = '- %s%s' % (body_text, ('（%s）' % when) if when else '')
        body.append(line)
    return lines + body + event_claim_lines(event)


def render_events(ranked, grouped, scored, events, picked_events, budget, rank_mode,
                  day_str, subnotes_on=True, event_top=0, header=HEADER, reserve=90,
                  label_of=None, now=None, tiered=False, window=0):
    """Event delivery: whole events, ranked, until the budget is spent.

    rank_mode (event_rank) treats an event as the delivery unit: when the next
    event does not fit, delivery stops -- no summary is substituted for原文.
    The packing mode (event) may fold a long event into its subnotes, and the
    tail beyond event_top is reported as folded rather than dropped silently.
    """
    limit = max(100, int(budget) - reserve)
    # 同一区间只交付一份（标签由 label_of 决定，默认是 start-end）
    picked_events = dedupe_picked(picked_events, events, label_of)
    subnotes_by_event = {}
    for ev in events or []:
        eid = str(ev.get('event_id') or '')
        if eid and ev.get('subnotes'):
            subnotes_by_event[eid] = ev.get('subnotes') or []

    def subnotes_for(event_id):
        return subnotes_by_event.get(str(event_id or ''), [])

    lines, folded, folded_events = [], [], []
    used = len(header)
    shown_events = 0
    blocks = []                     # one entry per delivered event, for budget trimming
    events_by_id = {str(ev.get('event_id') or ''): ev for ev in (events or [])
                    if isinstance(ev, dict)}
    for key in picked_events:
        members = sorted(grouped[key]['rows'], key=lambda item: float(item[1].get('ts') or 0.0))
        if tiered and members:
            # 时间分层：只改"每件事渲染多少"，不改入选顺序
            oldest = float(members[0][1].get('ts') or 0.0)
            rendered = tiered_event_lines(events_by_id.get(str(key)) or {}, members,
                                          day_str, tier_of(oldest, now))
        else:
            rendered = event_block_lines(events_by_id.get(str(key)) or {}, members, key,
                                         day_str)
        cost = sum(len(line) + 1 for line in rendered)
        if used + cost <= limit:
            lines.extend(rendered)
            used += cost
            shown_events += 1
            blocks.append((key, rendered, []))
            continue
        if rank_mode:
            # 事件是交付单位：装不下就停，绝不用摘要替换原文
            break
        if subnotes_on and grouped[key].get('rows') and subnotes_for(key):
            compressed = _compress_event(members, subnotes_for(key), used, limit, day_str)
            if compressed:
                lines.extend(compressed['lines'])
                used += compressed['used']
                folded.extend(compressed['compressed_ids'])
                shown_events += 1
                blocks.append((key, compressed['lines'], compressed['compressed_ids']))
                continue
        label = key if not key.startswith('ID:') else key[3:]
        folded_events.append(label)
        folded.extend(str(row.get('id') or '') for _s, row in members)
        line = '- %s 共%d句，已折叠' % (label, len(members))
        if used + len(line) + 1 <= limit:      # the trailer already reports the count
            lines.append(line)
            used += len(line) + 1
    if len('\n'.join(lines)) > DELIVERY_WARN_CHARS:
        # 不裁剪：只提醒调用方把事件数调小
        logger.warning('[delivery] 交付块过长 %d 字（上限仅提示，不建议超过）',
                       len('\n'.join(lines)))
    if event_top > 0 and not rank_mode:
        for key in ranked[event_top:]:
            label = key if not key.startswith('ID:') else key[3:]
            folded_events.append(label)
            folded.extend(str(row.get('id') or '') for _s, row in grouped[key]['rows'])

    def _trailer(rows):
        out = []
        if folded:
            stamps = [float(row.get('ts') or 0.0) for _s, row in scored if row.get('ts')]
            a = day_str(min(stamps)) if stamps else ''
            b = day_str(max(stamps)) if stamps else ''
            span = ('%s 至 %s' % (a, b)) if a and b and a != b else (a or '无时间')
            out.append('另有 %d 条相关记忆（%s，相关度 %.2f-%.2f，id 见 meta）'
                       % (len(folded), span,
                          min([s for s, _r in scored] or [0.0]),
                          max([s for s, _r in scored] or [0.0])))
            out.append('已折叠 %d 条（%d 个事件）' % (len(folded), len(folded_events)))
        return out

    body = header + CHR + CHR.join(lines + _trailer(lines))
    # the caller budget is a ceiling, not a hint: while the body is over it,
    # push the last delivered event back into the folded bucket and rebuild
    while len(body) > budget and lines and not body.startswith(header + CHR + '已折叠') :
        lines.pop()
        body = header + CHR + CHR.join(lines + _trailer(lines))
    while len(body) > budget and blocks:
        key, block_lines, compressed_ids = blocks.pop()
        for line in block_lines:
            if line in lines:
                lines.remove(line)
        folded.extend(compressed_ids)
        members = grouped[key]['rows']
        folded.extend(str(row.get('id') or '') for _s, row in members
                      if str(row.get('id') or '') not in set(folded))
        label = key if not key.startswith('ID:') else key[3:]
        folded_events.append(label)
        shown_events -= 1
        body = header + CHR + CHR.join(lines + _trailer(lines))
    if len(body) > budget:
        # nothing left to give up: keep only the header and the trailer
        body = header + CHR + CHR.join(_trailer(lines))
        body = body[:budget]

    body = header + CHR + CHR.join(lines)
    shown_ids = []
    for key in picked_events[:shown_events]:
        for _s, row in grouped[key]['rows']:
            shown_ids.append(str(row.get('id') or ''))
    if not lines and not shown_ids:
        # 同上：没有正文行就不交付空壳（事件全被折叠也一样）。
        return _empty_block('event_rank' if rank_mode else 'event',
                            sum(len(grouped[key]['rows']) for key in picked_events),
                            folded)
    # mode names the delivery that actually ran: event_rank (ranked events,
    # no compression) versus event (budget packing, may fold into subnotes)
    return {'text': body, 'chars': len(body),
            'mode': 'event_rank' if rank_mode else 'event',
            'events_picked': len(picked_events), 'events_shown': shown_events,
            'kept': sum(len(grouped[key]['rows']) for key in picked_events),
            'shown': len(shown_ids), 'folded': len(folded),
            'folded_ids': folded, 'folded_events': folded_events,
            'events': [{'event_id': grouped[key]['key'],
                        'max_cos': grouped[key]['max_cos'],
                        'rest_mean': grouped[key]['rest_mean'],
                        'score': grouped[key]['score'],
                        'sentence_count': grouped[key]['sentence_count'],
                        'best_entry_id': grouped[key]['best_id'],
                        'shown': position < shown_events}
                       for position, key in enumerate(picked_events)],
            'ids': shown_ids,
            # 事件路径没有窗口：交付的每一行都是命中行
            'hits': list(shown_ids)}
