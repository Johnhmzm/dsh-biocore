# -*- coding: utf-8 -*-
"""事件线程：用「共有人物 / 时间 / 主题」把事件连起来，写进**附件表**，不碰事件正文。

现状：`events.thread` 28/28 全空（payload 与 events_fourlayer 两处都是空串）。
本模块不改这两处，只新增两张表：

  event_thread_links  一个事件一行：它属于哪条线程、置信度、凭什么（factors）
  event_thread_edges  两个事件一条边：权重、凭据（共有人物/主题/时间/词面）

四路证据（都在本文件里算，不依赖模型、不联网）：
  ent    共有人物：claims 里的 subject/entity 集合**包含度**（共有 / 较小的那一边）
  topic  共同主题：claims 里的 thread 标签取二元字后按频率加权（标签字符串本身几乎
         每事件独一份，直接比相等会永远连不上；比标签里的词才连得上）
  time   时间近：同一 day 内按 ordinal 距离，跨 day 用真实时间戳，6 小时外不给分
  lex    词面近：ovation+text 的共享二元字，按「在全库里越少见越值钱」加权（所以
         "用户""对话"这种随处可见的词几乎不贡献分，不会被连成一片）

只增不改：
  - 两张表 CREATE TABLE IF NOT EXISTS；
  - 写入 INSERT OR REPLACE 到**自己的**表（主键是 event_id / (a,b)），
    events / events_fourlayer / event_sentences 一个字节都不动；
  - 重跑结果相同（纯函数，无随机数）。
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

DDL = (
    'CREATE TABLE IF NOT EXISTS event_thread_links ('
    ' event_id TEXT PRIMARY KEY, thread TEXT, day TEXT, ordinal INTEGER,'
    ' confidence REAL, degree INTEGER, factors TEXT, updated_at REAL)',
    'CREATE INDEX IF NOT EXISTS idx_event_threads_thread'
    ' ON event_thread_links(thread)',
    'CREATE TABLE IF NOT EXISTS event_thread_edges ('
    ' a TEXT, b TEXT, weight REAL, factors TEXT, added_at REAL,'
    ' PRIMARY KEY(a, b))',
    'CREATE INDEX IF NOT EXISTS idx_event_threads_edge_a'
    ' ON event_thread_edges(a)',
)

# 权重：人物与主题是"谁/什么事"，时间是"什么时候"，词面兜住前两路都缺的事件。
# 时间只占 0.10，而门槛是 0.12 —— **单靠时间连不成一条边**。
# 这是有意的：相邻两轮不代表同一件事，否则图会退化成一串"谁都连着一段"的链。
W_ENT = 0.40
W_TOPIC = 0.35
W_LEX = 0.15
W_TIME = 0.10
MIN_WEIGHT = 0.12
MAX_NEIGHBORS = 3
TIME_WINDOW_H = 6.0

YES = ('1', 'true', 'on', 'yes')


def _clip(x, low=0.0, high=1.0):
    return max(low, min(high, float(x)))


def _bigrams(text):
    body = ''.join(ch for ch in str(text or '') if ch.isalnum() or ch > '\u2e80')
    body = ''.join(ch for ch in body if not ch.isdigit())
    return {body[i:i + 2] for i in range(0, max(0, len(body) - 1))}


def _contain(a, b):
    """共有人物用「包含度」而不是 Jaccard：一边只提一个实体、另一边提五个时，
    那一个实体就是真的共有，不该被并集稀释成 1/5。"""
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def read_events(db_path):
    """读事件：payload 里的 claims/thread + events_fourlayer 的 day/ordinal。"""
    path = Path(str(db_path))
    if not path.exists():
        return []
    uri = 'file:%s?mode=ro' % str(path).replace('\\', '/')
    try:
        conn = sqlite3.connect(uri, uri=True)
    except Exception:
        return []
    rows = []
    try:
        meta = {}
        try:
            for eid, day, ordinal in conn.execute(
                    'SELECT event_id, day, ordinal FROM events_fourlayer'):
                meta[str(eid)] = (str(day or ''), int(ordinal or 0))
        except Exception:
            meta = {}
        for eid, payload, overview, text, started, ended in conn.execute(
                'SELECT id, payload, overview, text, started_ts, ended_ts'
                ' FROM events ORDER BY start'):
            try:
                doc = json.loads(payload) if payload else {}
            except Exception:
                doc = {}
            if not isinstance(doc, dict):
                doc = {}
            day, ordinal = meta.get(str(eid), ('', 0))
            claims = doc.get('claims') if isinstance(doc.get('claims'), list) else []
            ents, topics = set(), set()
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                for key in ('subject', 'entity'):
                    value = str(claim.get(key) or '').strip()
                    if value:
                        ents.add(value)
                topic = str(claim.get('thread') or '').strip()
                if topic:
                    topics.add(topic)
            rows.append({
                'event_id': str(eid),
                'day': day or str(doc.get('day') or ''),
                'ordinal': ordinal or int(doc.get('ordinal') or 0),
                'started': float(started) if isinstance(started, (int, float)) else 0.0,
                'ended': float(ended) if isinstance(ended, (int, float)) else 0.0,
                'overview': str(overview or '').strip(),
                'text': str(text or doc.get('text') or ''),
                'payload_thread': str(doc.get('thread') or '').strip(),
                'ents': ents,
                'topics': topics,
                'claims_thread': '',
            })
    except Exception:
        return []
    finally:
        conn.close()
    return rows


def _lex_index(events):
    """二元字 → 出现次数（全库），用来压低"到处都是"的词。"""
    counts = {}
    sets = []
    for event in events:
        grams = _bigrams(event['overview'] + ' ' + event['text'][:400])
        sets.append(grams)
        for gram in grams:
            counts[gram] = counts.get(gram, 0) + 1
    return counts, sets


def _lex_score(counts, ga, gb):
    shared = ga & gb
    if not shared:
        return 0.0, []
    total = 0.0
    kept = []
    for gram in shared:
        weight = 1.0 / math.log(2.0 + counts.get(gram, 1))
        total += weight
        kept.append((gram, round(weight, 3)))
    kept.sort(key=lambda row: -row[1])
    norm = min(len(ga), len(gb)) or 1
    return _clip(total / float(norm)), [row[0] for row in kept[:6]]


def _time_score(a, b):
    if a['day'] and a['day'] == b['day'] and a['ordinal'] and b['ordinal']:
        gap = abs(int(a['ordinal']) - int(b['ordinal']))
        if gap <= 1:
            return 1.0
        return 1.0 / (1.0 + gap)
    if a['started'] and b['started']:
        gap_h = abs(float(a['started']) - float(b['started'])) / 3600.0
        if gap_h <= TIME_WINDOW_H:
            return _clip(1.0 - gap_h / TIME_WINDOW_H)
    return 0.0


def pair_factors(a, b, counts=None, ga=None, gb=None, ta=None, tb=None):
    """一条边的四路凭据。

    主题不看标签字符串是否相等（每个事件的标签几乎是独一份的，相等等于永不相连），
    而是看标签**内部的词**有没有重合（比如两条都提到 3082），并按全库频率加权。
    """
    ent = _contain(a['ents'], b['ents'])
    topic, topic_grams = (0.0, [])
    if counts is not None and ta is not None and tb is not None:
        topic, topic_grams = _lex_score(counts, ta, tb)
    tim = _time_score(a, b)
    lex, grams = (0.0, [])
    if counts is not None and ga is not None and gb is not None:
        lex, grams = _lex_score(counts, ga, gb)
    weight = (W_ENT * ent + W_TOPIC * topic + W_TIME * tim + W_LEX * lex)
    return {
        'weight': round(_clip(weight), 4),
        'factors': {
            'ent': sorted(a['ents'] & b['ents'])[:6],
            'ent_score': round(ent, 4),
            'topic': topic_grams,
            'topic_score': round(topic, 4),
            'time_score': round(tim, 4),
            'lex': grams,
            'lex_score': round(lex, 4),
        },
    }


def plan(db_path, min_weight=MIN_WEIGHT, max_neighbors=MAX_NEIGHBORS):
    """算出全部边与每个事件的线程标签（只读，不写库）。"""
    events = read_events(db_path)
    if not events:
        return {'events': 0, 'edges': [], 'links': {}, 'connected': 0,
                'avg_degree': 0.0, 'components': []}
    counts, sets = _lex_index(events)
    grams_of = {event['event_id']: sets[i] for i, event in enumerate(events)}
    topic_of = {event['event_id']: _bigrams(' '.join(sorted(event['topics'])))
                for event in events}
    edges = []
    for i in range(len(events)):
        for j in range(i + 1, len(events)):
            a, b = events[i], events[j]
            got = pair_factors(a, b, counts, grams_of[a['event_id']],
                               grams_of[b['event_id']],
                               topic_of[a['event_id']], topic_of[b['event_id']])
            if got['weight'] >= float(min_weight):
                edges.append({'a': a['event_id'], 'b': b['event_id'],
                              'weight': got['weight'],
                              'factors': got['factors']})
    # 每个点只留最重的 max_neighbors 条，避免"人人相连"的糊状图
    keep = []
    by_node = {}
    for edge in edges:
        by_node.setdefault(edge['a'], []).append(edge)
        by_node.setdefault(edge['b'], []).append(edge)
    allowed = set()
    for _node, rows in by_node.items():
        rows = sorted(rows, key=lambda e: -e['weight'])[:int(max_neighbors)]
        for edge in rows:
            allowed.add((edge['a'], edge['b']))
    for edge in edges:
        if (edge['a'], edge['b']) in allowed:
            keep.append(edge)
    degree = {}
    for edge in keep:
        degree[edge['a']] = degree.get(edge['a'], 0) + 1
        degree[edge['b']] = degree.get(edge['b'], 0) + 1
    for event in events:
        event['degree'] = degree.get(event['event_id'], 0)
    links = label_threads(events, keep)
    nodes = len(events)
    connected = len([e for e in events if e['degree'] > 0])
    return {
        'events': nodes,
        'edges': keep,
        'edges_raw': len(edges),
        'links': links,
        'connected': connected,
        'avg_degree': round(sum(degree.values()) / float(nodes), 4) if nodes else 0.0,
        'avg_degree_connected': round(
            sum(degree.values()) / float(connected), 4) if connected else 0.0,
        'components': components(events, keep),
    }


def components(events, edges):
    parent = {event['event_id']: event['event_id'] for event in events}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in edges:
        ra, rb = find(edge['a']), find(edge['b'])
        if ra != rb:
            parent[rb] = ra
    groups = {}
    for event in events:
        groups.setdefault(find(event['event_id']), []).append(event['event_id'])
    return [sorted(rows) for rows in groups.values() if len(rows) > 1]


def label_threads(events, edges):
    """给每个事件定线程名：**同一组件共用一个名字**，名字取组件里最共有的那件东西。

    为什么不用事件自己的 claims.thread：那 28 个标签几乎每事件独一份
    （'桌面版dsh命令沿用'、'3082测试与改动'……），拿它当线程名等于没有线程。
    真正的线索是"这条链上的人/物反复出现"：所以组件内有共有实体就用实体名，
    没有就用标签里最常见的词，再没有就用「日期#段号」。保证每个事件都有名字。
    """
    by_id = {event['event_id']: event for event in events}
    groups = components(events, edges)
    label_of = {}
    for group in groups:
        members = [by_id[eid] for eid in group if eid in by_id]
        ent_count = {}
        for event in members:
            for ent in event['ents']:
                ent_count[ent] = ent_count.get(ent, 0) + 1
        best_ent = ''
        if ent_count:
            ranked = sorted(ent_count.items(), key=lambda kv: (-kv[1], kv[0]))
            if ranked[0][1] >= 2:
                best_ent = ranked[0][0]
        label = best_ent
        if not label:
            gram_count = {}
            for event in members:
                for gram in _bigrams(' '.join(sorted(event['topics']))):
                    gram_count[gram] = gram_count.get(gram, 0) + 1
            if gram_count:
                ranked = sorted(gram_count.items(), key=lambda kv: (-kv[1], kv[0]))
                if ranked[0][1] >= 2:
                    label = ranked[0][0]
        if not label:
            first = members[0] if members else {}
            label = '%s#段%d' % (first.get('day') or 'unknown',
                                 int(first.get('ordinal') or 0) // 8)
        for eid in group:
            label_of[eid] = (label, 'component')
    links = {}
    for event in events:
        own = sorted(event['topics'])
        label, source = label_of.get(event['event_id'], ('', ''))
        if not label:
            if own:
                label, source = own[0], 'own'
            else:
                label = '%s#段%d' % (event['day'] or 'unknown',
                                     int(event['ordinal'] or 0) // 8)
                source = 'temporal'
        links[event['event_id']] = {
            'thread': label,
            'day': event['day'],
            'ordinal': int(event['ordinal'] or 0),
            'degree': int(event.get('degree') or 0),
            'confidence': round(min(1.0, 0.4 + 0.2 * int(event.get('degree') or 0)), 3),
            'factors': {'source': source, 'own_topics': own[:6]},
        }
    return links


def write(db_path, plan_doc, now=None):
    """写两张附件表（只 INSERT OR REPLACE 自己的表）。返回写入行数。"""
    path = Path(str(db_path))
    if not path.exists():
        return {'links': 0, 'edges': 0}
    stamp = float(now if now is not None else time.time())
    conn = sqlite3.connect(str(path))
    try:
        for ddl in DDL:
            conn.execute(ddl)
        wrote_links = 0
        for event_id, row in (plan_doc.get('links') or {}).items():
            conn.execute(
                'INSERT OR REPLACE INTO event_thread_links'
                ' (event_id, thread, day, ordinal, confidence, degree, factors,'
                ' updated_at) VALUES (?,?,?,?,?,?,?,?)',
                (str(event_id), str(row.get('thread') or ''), str(row.get('day') or ''),
                 int(row.get('ordinal') or 0), float(row.get('confidence') or 0.0),
                 int(row.get('degree') or 0),
                 json.dumps(row.get('factors') or {}, ensure_ascii=False), stamp))
            wrote_links += 1
        wrote_edges = 0
        for edge in (plan_doc.get('edges') or []):
            conn.execute(
                'INSERT OR REPLACE INTO event_thread_edges'
                ' (a, b, weight, factors, added_at) VALUES (?,?,?,?,?)',
                (str(edge['a']), str(edge['b']), float(edge['weight']),
                 json.dumps(edge.get('factors') or {}, ensure_ascii=False), stamp))
            wrote_edges += 1
        conn.commit()
        return {'links': wrote_links, 'edges': wrote_edges}
    finally:
        conn.close()


def load(db_path):
    """读回来（交付/面板用）：{event_id: {thread, degree, confidence, ...}}"""
    path = Path(str(db_path))
    if not path.exists():
        return {}
    uri = 'file:%s?mode=ro' % str(path).replace('\\', '/')
    try:
        conn = sqlite3.connect(uri, uri=True)
    except Exception:
        return {}
    out = {}
    try:
        for row in conn.execute(
                'SELECT event_id, thread, day, ordinal, confidence, degree, factors'
                ' FROM event_thread_links'):
            out[str(row[0])] = {'thread': str(row[1] or ''), 'day': str(row[2] or ''),
                                'ordinal': int(row[3] or 0),
                                'confidence': float(row[4] or 0.0),
                                'degree': int(row[5] or 0),
                                'factors': json.loads(row[6] or '{}')}
    except Exception:
        return {}
    finally:
        conn.close()
    return out
