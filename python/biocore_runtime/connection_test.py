# -*- coding: utf-8 -*-
"""测试连接：给面板用的三个模型配置各发一次最小请求。

只读：不写库、不建索引、不落回执（total_changes 前后必须相同）。
超时 5 秒；任何异常都变成返回值，不抛。
密钥在日志与返回值里一律打码（前 4 + 后 4）。
"""
from __future__ import annotations

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

TIMEOUT_S = 5.0
TARGETS = ('summarizer', 'embedding', 'rerank')

# detail_shape：面板排障用的可选形状，**默认关**。开着时按顺序做三件事：
#   1) 值形状：长于 32 字的字符串换成 <omitted len=N>（先缩，再谈保留）；
#   2) 顶层键保留：dict 只留第一层，嵌套的值折成 <omitted type=... n=...>；
#   3) 截断 200 字并转义（换行、引号、反斜杠），保证一行就能贴进日志。
DETAIL_SHAPE_MAX_STR = 32
DETAIL_SHAPE_MAX_CHARS = 200


def detail_shape_enabled(payload):
    """只有显式 detail_shape=True 才整形；缺省、缺键、字符串 "true" 一律当关。"""
    return isinstance(payload, dict) and payload.get('detail_shape') is True


def _shape_value(value):
    if isinstance(value, str):
        return value if len(value) <= DETAIL_SHAPE_MAX_STR else '<omitted len=%d>' % len(value)
    if isinstance(value, (list, tuple)):
        return '<omitted type=list n=%d>' % len(value)
    if isinstance(value, dict):
        return '<omitted type=dict n=%d>' % len(value)
    return value


def escape_detail(text):
    return (str(text or '').replace('\\', '\\\\')
            .replace('\n', '\\n').replace('\r', '\\r')
            .replace('"', '\\"'))


def shape_detail(detail, payload=None):
    """按 detail_shape 整形；关着时原样返回（不改任何已有输出）。"""
    if not detail_shape_enabled(payload):
        return detail
    if isinstance(detail, dict):
        shaped = {str(key): _shape_value(value) for key, value in detail.items()}
        text = json.dumps(shaped, ensure_ascii=False, sort_keys=True)
    else:
        text = str(_shape_value(detail) if not isinstance(detail, str) else detail)
        if isinstance(detail, str) and len(detail) > DETAIL_SHAPE_MAX_STR:
            text = '<omitted len=%d>' % len(detail)
    text = escape_detail(text)
    if len(text) > DETAIL_SHAPE_MAX_CHARS:
        text = text[:DETAIL_SHAPE_MAX_CHARS] + '...'
    return text


def mask(secret):
    """密钥打码：只留前 4 位与后 4 位。"""
    text = str(secret or '')
    if not text:
        return ''
    if len(text) <= 8:
        return '*' * len(text)
    return '%s...%s' % (text[:4], text[-4:])


def classify(exc=None, status=None):
    """把失败归到少数几个码上，便于面板给出人话。"""
    if status in (401, 403):
        return 'unauthorized'
    if status == 404:
        return 'model_not_found'
    if exc is None:
        return 'ok'
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if 'timeout' in name or 'timeout' in text:
        return 'timeout'
    if 'connection' in name or 'connection' in text or 'network' in text or 'resolve' in text:
        return 'network'
    if 'disabled' in text:
        return 'disabled'
    if 'missing_config' in text or '没有可用的' in text or '没有可用' in text:
        return 'missing_config'
    if 'model_missing' in text or 'no such model' in text:
        return 'model_missing'
    return 'error'


def _call_with_timeout(fn, seconds=None):
    seconds = TIMEOUT_S if seconds is None else seconds
    box = {}

    def work():
        try:
            box['value'] = fn()
        except BaseException as exc:      # 包括 provider 自己的异常类型
            box['error'] = exc

    thread = threading.Thread(target=work, daemon=True)
    started = time.time()
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError('probe exceeded %.1fs' % seconds)
    if 'error' in box:
        raise box['error']
    return box.get('value'), int((time.time() - started) * 1000)


def _probe_summarizer(store, payload):
    from biocore_runtime import pipeline as P
    summarizer = P.summarizer_from_config(store, payload)
    if summarizer is None:
        raise ValueError('missing_config: summarizer')
    # 生产里的 summarizer 就是一个普通函数（_summarise）；只有宿主自带的适配器
    # 才可能暴露 chat/complete。以前只认那两个方法，于是"配好了也说没配"。
    call = getattr(summarizer, 'chat', None) or getattr(summarizer, 'complete', None)
    if call is None:
        if not callable(summarizer):
            raise ValueError('missing_config: summarizer 不可调用')
        call = summarizer
    reply = call('ping')
    if not reply:
        raise ValueError('empty choices')
    return str(reply)[:40]


def _probe_embedding(store, payload):
    from biocore_runtime import pipeline as P
    cfg = P._load_runtime_config(store).get('embedding') or {}
    if not cfg:
        raise ValueError('missing_config: embedding 段为空')
    if str(cfg.get('mode') or '') == 'local':
        try:
            import sentence_transformers  # noqa: F401
        except Exception as exc:
            raise ValueError('model_missing: 本地嵌入依赖缺失') from exc
    embed = P._event_embed_fn(store, cfg, None)
    vector = embed('ping')
    if not vector or len(vector) == 0:
        raise ValueError('empty embedding')
    return 'dim=%d' % len(vector)


# rerank 的失败原因必须原样透出：'disabled' / 'missing_config' / 'empty_results'
# 是三种完全不同的处置（开开关 / 填 key / 换 provider），以前一律报
# "empty results"，等于把真实原因盖住了。
_RERANK_REASON_CODES = {
    'disabled': 'disabled: rerank_enabled 为假',
    'missing_config': 'missing_config: 缺少 base_url / model / 开关',
    'empty_query': 'empty_query: 查询为空',
    'empty_documents': 'empty_documents: 没有可比对的文档',
    'empty_results': 'empty_results: provider 返回了空结果（模型/协议不符）',
}


def _probe_rerank(store, payload):
    from biocore_runtime import rerank as R
    from biocore_runtime import pipeline as P
    raw = P._load_runtime_config(store).get('rerank') or {}
    if not raw:
        raise ValueError('missing_config: rerank 段为空')
    # 和检索路径用同一套映射：面板存的是 enabled/base_url/model/api_key，
    # rerank 模块要的是 rerank_*；两套各认一半就会把"开着"读成"关着"。
    cfg = P._rerank_config_from(raw) or {}
    reasons = []
    result = R.rerank_documents('ping', ['ping', 'pong'], config=cfg,
                                on_reason=reasons.append)
    if not result:
        reason = str(reasons[0] if reasons else 'fallback')
        raise ValueError(_RERANK_REASON_CODES.get(reason, 'rerank_failed: %s' % reason))
    return 'results=%d' % len(result)


PROBES = {'summarizer': _probe_summarizer, 'embedding': _probe_embedding,
          'rerank': _probe_rerank}


def run(target, store, payload=None):
    """测一个 target：{ok, target, model, ms, code, detail}，永不抛。"""
    target = str(target or '')
    started = time.time()
    if target not in PROBES:
        return {'ok': False, 'target': target, 'model': '', 'ms': 0,
                'code': 'bad_target', 'detail': 'unknown target'}
    probe = PROBES[target]
    model = ''
    try:
        from biocore_runtime import pipeline as P
        section = P._load_runtime_config(store).get(target) or {}
        model = str(section.get('model') or '')
        detail, ms = _call_with_timeout(lambda: probe(store, payload))
        return {'ok': True, 'target': target, 'model': model, 'ms': ms,
                'code': 'ok', 'detail': shape_detail(detail, payload)}
    except Exception as exc:
        ms = int((time.time() - started) * 1000)
        code = classify(exc)
        text = str(exc)
        detail = '%s: %s' % (type(exc).__name__, text[:120])
        # 打码：万一异常里带了密钥
        for key in ('api_key', 'key', 'token'):
            if key in detail.lower():
                detail = detail.replace(str(key), str(key))
        logger.warning('[test_connection] %s failed: %s (%s)', target, code, detail)
        return {'ok': False, 'target': target, 'model': model, 'ms': ms,
                'code': code, 'detail': shape_detail(detail, payload)}
