# -*- coding: utf-8 -*-
"""Pure helpers for the note pipeline: relative-time normalisation and the
mandatory conflict banner. No model calls, no lexicon beyond the time words
the spec names.

Time grammar lives here and only here (docs/CORE-CONTRACT.md: closed-class time
deixis is grammar, not domain knowledge). first_date() is the one parser the cue
layer calls; normalize_relative_time() is the renderer the note pipeline uses.
Both read the same table, so a phrase can no longer mean two different days.

变更说明（2026-11-12）：明天 / 后天 / 大后天 的偏移此前与命名错位——本模块写的是
明天=-2、后天=-1（按"往回几天"存），index 里另一张表方向相反且缺 大后天，结果
"明天"渲染成后天、"后天"渲染成明天、"大后天"被吃成「大 + 后天」。现在只有一张表，
统一为相对基准日的天数增量：大前天=-3、明天=+1、后天=+2、大后天=+3；表按词长降序，
大后天 先于 后天 匹配，不再留残字。
"""
from __future__ import annotations

import datetime as _dt
import re
from datetime import datetime, timedelta

_WEEK = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}
# Days relative to the reference day (negative = past). Longest phrase first:
# 大后天 must be tried before 后天, or the match eats 后天 and leaves a stray 大.
_DAY_WORDS = (('大前天', -3), ('大后天', 3), ('前天', -2), ('后天', 2),
              ('昨天', -1), ('今天', 0), ('明天', 1))
_CN_NUM = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
_ABS_DATE = re.compile(r'(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})')
_CN_DATE = re.compile(r'(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*月\s*'
                      r'(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*[日号]?')
_DAYS_AGO = re.compile(r'(\d{1,3}|[零一二两三四五六七八九十]{1,3})\s*天\s*(前|后)')
_WEEK_DAY = re.compile(r'(上周|本周|这周|下周|上星期|本星期|下星期)\s*([一二三四五六日天])')
_WEEK_SHIFT = {'上周': -7, '上星期': -7, '本周': 0, '这周': 0, '本星期': 0,
               '下周': 7, '下星期': 7}
# One alternation, longest first: replacing phrase by phrase let a later short
# phrase match inside an earlier annotation ("（原文：大后天）" contains 后天).
_DAY_OFFSET = dict(_DAY_WORDS)
_DAY_PATTERN = re.compile('|'.join(re.escape(phrase) for phrase, _ in _DAY_WORDS))


def _fmt(moment):
    return moment.strftime('%Y-%m-%d')


def _replace(text, phrase, day):
    return text.replace(phrase, '%s（原文：%s）' % (_fmt(day), phrase))


def normalize_relative_time(text, ts):
    """Rewrite relative day words into absolute dates anchored at ts."""
    raw = str(text or '')
    try:
        base = datetime.fromtimestamp(float(ts))
    except Exception:
        return raw
    out = _DAY_PATTERN.sub(
        lambda match: _replace(match.group(0), match.group(0),
                               base + timedelta(days=_DAY_OFFSET[match.group(0)])),
        raw)
    out = re.sub(r'(\d+)\s*天前', lambda m: '%s（原文：%s天前）' % (
        _fmt(base - timedelta(days=int(m.group(1)))), m.group(1)), out)
    # 上周X / 本周X / 下周X
    def week(match):
        which, weekday = match.group(1), match.group(2)
        target = _WEEK.get(weekday)
        if target is None:
            return match.group(0)
        monday = base - timedelta(days=base.weekday())
        offset = {'上周': -7, '本周': 0, '这周': 0, '下周': 7}.get(which, 0)
        day = monday + timedelta(days=offset + target)
        return '%s（原文：%s）' % (_fmt(day), match.group(0))
    out = re.sub(r'(上周|本周|这周|下周)([一二三四五六日天])', week, out)
    return out


def _cn_int(text):
    """Small Chinese numeral reader (十, 十八, 二十三); None when unreadable."""
    value = str(text or '').strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if '十' not in value:
        return _CN_NUM.get(value)
    head, _, tail = value.partition('十')
    if head and head not in _CN_NUM:
        return None
    if tail and tail not in _CN_NUM:
        return None
    tens = _CN_NUM.get(head, 1) if head else 1
    ones = _CN_NUM.get(tail, 0) if tail else 0
    return tens * 10 + ones


def _anchor(ref_ts):
    """Reference moment for a relative word; None means now, "" means broken."""
    if ref_ts is None or ref_ts == '':
        return _dt.datetime.now()
    try:
        return _dt.datetime.fromtimestamp(float(ref_ts))
    except Exception:
        return None


def _iso(year, month, day):
    try:
        return _dt.date(int(year), int(month), int(day)).isoformat()
    except Exception:
        return ''


def first_date(text, ref_ts=None) -> str:
    """First date the text names, as YYYY-MM-DD; "" when nothing parses.

    Order: absolute date, 月日, closed-class day words, N 天前/后, 上周X.
    Unrecognised input returns "" -- it never raises and never invents a day.
    """
    raw = str(text or '')
    if not raw.strip():
        return ''
    base = _anchor(ref_ts)
    if base is None:
        return ''
    try:
        hit = _ABS_DATE.search(raw)
        if hit:
            return _iso(int(hit.group(1)), int(hit.group(2)), int(hit.group(3)))
        hit = _CN_DATE.search(raw)
        if hit:
            month, day = _cn_int(hit.group(1)), _cn_int(hit.group(2))
            if month and day:
                return _iso(base.year, month, day)
        for phrase, delta in _DAY_WORDS:
            if phrase in raw:
                return (base + _dt.timedelta(days=delta)).date().isoformat()
        hit = _DAYS_AGO.search(raw)
        if hit:
            count = _cn_int(hit.group(1))
            if count:
                sign = -1 if hit.group(2) == '前' else 1
                return (base + _dt.timedelta(days=sign * count)).date().isoformat()
        hit = _WEEK_DAY.search(raw)
        if hit:
            which, weekday = hit.group(1), hit.group(2)
            target = _WEEK.get(weekday)
            if target is not None:
                monday = base.date() - _dt.timedelta(days=base.weekday())
                shift = _WEEK_SHIFT.get(which, 0)
                return (monday + _dt.timedelta(days=shift + target)).isoformat()
    except Exception:
        return ''
    return ''


def conflict_lines(state):
    """Unconditional banner for every attribute holding more than one value."""
    lines = []
    for key, item in sorted((state or {}).items()):
        if not isinstance(item, dict):
            continue
        values = []
        for value in (item.get('coexist') or []) + [item.get('value')]:
            value = str(value or '').strip()
            if value and value not in values:
                values.append(value)
        if len(values) < 2:
            continue
        subject = key.split('·')[0]
        shown = ' / '.join('%s（第%s轮）' % (value, item.get('valid_at_turn') or '?')
                           for value in values)
        lines.append('【注意】%s 有两种说法：%s，记录不足以确定，需向用户确认'
                     % (key, shown))
    return lines
