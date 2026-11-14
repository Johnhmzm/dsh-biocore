# -*- coding: utf-8 -*-
"""Scoring and ranking: turn a candidate pool into an ordered event list.

Pure functions only -- no store, no file, no environment. The runtime supplies
the vectors, the seeds, the weights and the relative-cut function; this module
decides what the numbers are and in what order the events come out.

Three event score modes exist because they answer different questions and were
measured separately (see docs/METRICS.md): max (one good line), lse (a smoothed
max that lets a second line lift the score), countrank (sentence count by rank,
so a long event cannot win by sheer volume).
"""
from __future__ import annotations

import math

from . import channels as _ch

TOP_GLOBAL = 40          # lines above this rank feed the in_top40 tie-breaker
MODE_MAX = 'max'
MODE_COUNTRANK = 'countrank'


def _score_rows_numpy(pool, qvecs):
    """批量余弦：预归一化后一次矩阵乘；不可用（无 numpy / 无向量 / 维度不齐）返回 None。

    只做算术，不改语义：每行取"对各查询路线的最大余弦"，与逐条 channels.cosine 同值；
    零向量按 0.0（与 channels.cosine 的 na/nb 守卫一致）。
    """
    if not qvecs or not pool:
        return None
    try:
        import numpy as np
    except Exception:
        return None
    dim = None
    rows_vectors = []
    for row in pool:
        vector = (row or {}).get('vector')
        if not isinstance(vector, (list, tuple)) or not vector:
            return None
        if dim is None:
            dim = len(vector)
        elif len(vector) != dim:
            return None
        rows_vectors.append(vector)
    try:
        matrix = np.asarray(rows_vectors, dtype=np.float64)
        queries = np.asarray([list(q) for q in qvecs if isinstance(q, (list, tuple)) and q],
                             dtype=np.float64)
    except Exception:
        return None
    if queries.ndim != 2 or queries.shape[1] != dim:
        return None
    rows_norm = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(queries, axis=1)
    safe_rows = np.where(rows_norm > 0, rows_norm, 1.0)
    safe_queries = np.where(query_norm > 0, query_norm, 1.0)
    unit_rows = matrix / safe_rows[:, None]
    unit_queries = queries / safe_queries[:, None]
    sims = unit_rows @ unit_queries.T                      # (rows, routes)
    best = sims.max(axis=1)
    best = np.where((rows_norm > 0) & (query_norm > 0).any(), best, 0.0)
    return [(float(value), row) for value, row in zip(best, pool)]


def score_rows(pool, qvecs, seeds, text, mood_weight=0.0, mood_now=0.0):
    """Score every row against every query route; the best route wins.

    With vectors, each row takes the max cosine over the query variants. Without
    them the seeds compete lexically: the summary route can only add evidence,
    never take the question's own route away. mood_weight adds mood-congruent
    recall (Bower 1981) from the row's own affect label, and is inert without one.
    """
    if qvecs and not mood_weight:
        fast = _score_rows_numpy(pool, qvecs)
        if fast is not None:
            return fast
    scored = []
    for row in pool or []:
        if qvecs:
            vector = row.get('vector') or []
            value = max((_ch.cosine(qv, vector) for qv in qvecs), default=0.0)
        else:
            value = max((_ch.lexical_score(piece, str(row.get('text') or ''))
                         for piece in (seeds or [text])), default=0.0)
        affect = row.get('affect')
        if mood_weight and isinstance(affect, dict):
            try:
                env_v = float(affect.get('v', affect.get('valence')) or 0.0)
                value = float(value) + mood_weight * (1.0 - abs(env_v - mood_now) / 2.0)
            except Exception:
                pass
        scored.append((float(value), row))
    return scored


def group_events(scored):
    """Bucket scored rows by event (its id, or ID:<row id> when it has none).

    Order is first-seen order, so the final sort stays stable for equal scores.
    """
    grouped = {}
    order = []
    for score, row in scored or []:
        key = str(row.get('event_id') or '') or ('ID:' + str(row.get('id') or ''))
        if key not in grouped:
            grouped[key] = {'key': key, 'rows': [], 'best': -1.0, 'best_id': '',
                            'start': float(row.get('ts') or 0.0)}
            order.append(key)
        bucket = grouped[key]
        bucket['rows'].append((score, row))
        first = float(row.get('ts') or 0.0)
        if first and (not bucket['start'] or first < bucket['start']):
            bucket['start'] = first
        if score > bucket['best']:
            bucket['best'] = score
            bucket['best_id'] = str(row.get('id') or '')
    return grouped, order


def score_events(grouped, order, scored, rel_cut, mode=MODE_MAX,
                 w_max=0.9, w_rest=0.1, w_count=0.3, beta_fn=None):
    """Fill each bucket's score and diagnostics; returns the lse snapshot or None.

    rel_cut(values) is the caller's relative threshold (it reads the ratio/floor
    settings). beta_fn() is only called in lse mode: the caller keeps the beta
    lookup (env + stored value), this module only does the arithmetic.
    """
    global_scores = sorted((score for score, _row in scored or []), reverse=True)
    top = set(global_scores[:TOP_GLOBAL])
    for key in order:
        bucket = grouped[key]
        values = sorted((score for score, _row in bucket['rows']), reverse=True)
        rel = rel_cut(values)
        best = float(values[0]) if values else 0.0
        rest = values[1:]
        # a single-line event must not be punished for having no rest:
        # rest_mean falls back to its own maximum
        rest_mean = sum(rest) / len(rest) if rest else best
        bucket['max_cos'] = round(best, 6)
        bucket['rest_mean'] = round(rest_mean, 6)
        bucket['sentence_count'] = len(values)
        bucket['relevant'] = sum(1 for value in values if value >= rel)
        bucket['score'] = round(w_max * best + w_rest * rest_mean, 6)
        bucket['in_top40'] = sum(1 for value in values if value in top)
    if mode == MODE_MAX:
        # 只用最高句：事件内的其他句子不参与计分（多句累加等价于奖励句数，
        # 而金标事件的命中句数在三个人身上都不占优）
        for key in order:
            bucket = grouped[key]
            values = [v for v, _r in bucket['rows']]
            bucket['score'] = round(max(values) if values else 0.0, 6)
        return None
    if mode == MODE_COUNTRANK:
        by_count = sorted(order, key=lambda k: -grouped[k]['relevant'])
        rank_of, last_count, rank = {}, None, 0
        for index, key in enumerate(by_count, 1):
            count = grouped[key]['relevant']
            if count != last_count:
                rank, last_count = index, count
            rank_of[key] = rank
        n = max(1, len(order))
        for key in order:
            bucket = grouped[key]
            norm = 1.0 - (rank_of[key] - 1) / float(n - 1) if n > 1 else 1.0
            bucket['count_rank'] = rank_of[key]
            bucket['count_norm'] = round(norm, 6)
            bucket['score'] = round((1.0 - w_count) * bucket['max_cos']
                                    + w_count * norm, 6)
        return None
    return None


def rank_events(grouped, order):
    """Stable order: score, then how many lines of the event are in the global
    top 40, then start time, then first-seen order."""
    return sorted(order, key=lambda key: (-grouped[key]['score'],
                                          -grouped[key]['in_top40'],
                                          grouped[key]['start'],
                                          order.index(key)))
