# -*- coding: utf-8 -*-
"""面板的设置读写（后端动作）：读三处模型配置 + 七个设置；写回 core_config.json。

三条硬规矩：
  1) **密钥永不整串返回**：一律 mask（前 4 + 后 4）；比 8 位短的全星号。
  2) 写前备份 `.bak-<时间戳>`；写后立刻**读回校验**磁盘上的值与请求一致。
  3) 运行期生效、不要求重启：写的就是 `_load_runtime_config` 每次读的那个文件。

只做设置，不碰记忆内容；不调模型（测试按钮走既有的 connection_test）。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import connection_test as _ct

CONFIG_FILE = 'core_config.json'

# 三个模型 target -> 允许写的字段
TARGET_FIELDS = {
    'summarizer': ('base_url', 'model', 'key', 'timeout', 'thinking'),
    'embedding': ('base_url', 'model', 'key', 'mode', 'timeout'),
    'rerank': ('base_url', 'model', 'key', 'enabled', 'top_n', 'timeout'),
}

# 七个设置：名字 -> (类型, 下限, 上限) 或 ('bool',)
SETTINGS_SPEC = {
    'tiered_delivery_on': ('int', 0, 1),
    'portrait_on': ('int', 0, 1),
    'consolidate_on': ('int', 0, 1),
    'detail_shape': ('bool',),
    'event_preset': ('int', 1, 20),
    'window': ('int', 0, 2),
    'portrait_top': ('int', 1, 50),
    # 事件交付的相关性门槛（0..1）与干净交付路径开关（0/1）
    'relevant_floor': ('float', 0.0, 1.0),
    'event_delivery_clean_on': ('int', 0, 1),
    # 交付用原文还是总结：raw / summary / auto（字符串白名单）
    'event_text_mode': ('str', ('raw', 'summary', 'auto')),
    'long_event_chars': ('int', 200, 8000),
    # 压缩阈值（用户可改，不靠改 YAML）：绝对 token 门槛 / 窗口比例 / 窗口兜底。
    # 0 一律表示"关"：0/0 时退回到官方 YAML 的 thresholdRatio（与本文件无关）。
    'compaction_threshold_tokens': ('int', 0, 2000000),
    'compaction_threshold_ratio': ('float', 0.0, 0.95),
    'compaction_context_window': ('int', 0, 4000000),
    # 记忆注入等召回的上限（毫秒）：0 = 一直等到召回完成；缺省 10000（先等再答）
    'inject_wait_ms': ('int', 0, 60000),
    # 缓存有效期（毫秒）：同一句提问在 TTL 内直接复用，不重新召回，缺省 15000
    'inject_ttl_ms': ('int', 0, 600000),
    # 向量兜底：turn_end / 压缩写入后各补一次少量缺失向量（缺省开）
    'vector_backfill_on': ('int', 0, 1),
}

# 设置名 -> recall 段里的键（detail_shape 是面板自己的开关，不进 recall）
RECALL_KEYS = {
    'tiered_delivery_on': 'recall.tiered_delivery_on',
    'portrait_on': 'recall.portrait_on',
    'consolidate_on': 'recall.consolidate_on',
    'event_preset': 'recall.event_preset',
    'window': 'recall.window',
    'portrait_top': 'recall.portrait_top',
    'relevant_floor': 'recall.relevant_floor',
    'event_delivery_clean_on': 'recall.event_delivery_clean_on',
    'event_text_mode': 'recall.event_text_mode',
    'long_event_chars': 'recall.long_event_chars',
    'compaction_threshold_tokens': 'recall.compaction_threshold_tokens',
    'compaction_threshold_ratio': 'recall.compaction_threshold_ratio',
    'compaction_context_window': 'recall.compaction_context_window',
    'inject_wait_ms': 'recall.inject_wait_ms',
    'inject_ttl_ms': 'recall.inject_ttl_ms',
    'vector_backfill_on': 'recall.vector_backfill_on',
}


def mask(secret):
    return _ct.mask(secret)


def _config_path(store):
    from biocore_runtime import pipeline as P
    data_dir = P._store_data_dir(store)
    return None if data_dir is None else Path(data_dir) / CONFIG_FILE


def _read_config(path):
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _model_value(block, field):
    """读模型字段：面板存 key / api_key 两种写法都认（3082 里就是 api_key）。"""
    if not isinstance(block, dict):
        return None
    if field == 'key':
        return block.get('key') or block.get('api_key')
    return block.get(field)


def _masked_model(doc, target):
    block = doc.get(target) if isinstance(doc.get(target), dict) else {}
    fields = TARGET_FIELDS.get(target, ())
    out = {}
    for field in fields:
        value = _model_value(block, field)
        if field == 'key':
            out[field] = mask(value or '')
            out['has_key'] = bool(str(value or '').strip())
        elif field == 'enabled':
            out[field] = bool(value)
        else:
            out[field] = '' if value is None else value
    return out


def _status_block(store):
    """只读状态：修补命中率 / 最近一次总结 / 画像条数 / 访问日志今日增量。

    只读库与文件，不写、不调模型；任何一项失败就留空，绝不影响设置读取。
    """
    from biocore_runtime import pipeline as P
    out = {'repair': {}, 'consolidate': {}, 'portrait': {}, 'access_log': {}}
    try:
        from biocore_runtime import lifecycle as _lifecycle
        out['repair'] = _lifecycle.repair_stats(store)
    except Exception:
        pass
    root = P._store_root(store)
    if root is None:
        return out
    try:
        sessions = Path(P._store_data_dir(store)) / 'sessions'
        dbs = sorted(sessions.glob('*/memory.db')) if sessions.exists() else []
        if dbs:
            conn = __import__('sqlite3').connect(
                'file:%s?mode=ro' % str(dbs[0]).replace('\\', '/'), uri=True)
            try:
                row = conn.execute(
                    'SELECT status, code, updated_at, members FROM event_consolidation'
                    ' ORDER BY updated_at DESC LIMIT 1').fetchone()
            finally:
                conn.close()
            if row:
                out['consolidate'] = {'status': row[0], 'code': row[1],
                                      'updated_at': row[2], 'members': row[3]}
    except Exception:
        pass
    try:
        from biocore_memory.identity import portrait_store as _ps
        items = 0
        for session_dir in (Path(P._store_data_dir(store)) / 'sessions').glob('*'):
            try:
                items += len(_ps.view(session_dir).get('items') or [])
            except Exception:
                continue
        out['portrait'] = {'items': items}
    except Exception:
        pass
    try:
        from datetime import datetime
        midnight = datetime.now().replace(hour=0, minute=0, second=0,
                                          microsecond=0).timestamp()
        total = 0
        for db in (Path(P._store_data_dir(store)) / 'sessions').glob('*/memory.db'):
            conn = __import__('sqlite3').connect(
                'file:%s?mode=ro' % str(db).replace('\\', '/'), uri=True)
            try:
                total += int(conn.execute(
                    'SELECT COUNT(1) FROM access_log WHERE at >= ?', (midnight,)).fetchone()[0])
            except Exception:
                continue
            finally:
                conn.close()
        out['access_log'] = {'today': total}
    except Exception:
        pass
    return out


def settings_get(store, target=None):
    """读设置：三处模型配置（密钥打码）与七个设置当前值。"""
    path = _config_path(store)
    if path is None:
        return {'ok': False, 'code': 'no_store', 'detail': 'store 没有数据目录'}
    doc = _read_config(path)
    models = {name: _masked_model(doc, name) for name in TARGET_FIELDS}
    recall = doc.get('recall') if isinstance(doc.get('recall'), dict) else {}
    settings = {}
    for name, spec in SETTINGS_SPEC.items():
        if spec[0] == 'bool':
            settings[name] = bool(doc.get(name)) if name in doc else False
            continue
        raw = recall.get(name)
        if raw is None:
            # 缺键时给 SPECS 的**缺省值**（不是下限）：下限会让"没配过"看起来像"被改过"
            try:
                from biocore_memory.config import recall_params as _rp
                raw = _rp.SPECS[name][0]
            except Exception:
                raw = spec[1]
        settings[name] = raw
    return {'ok': True, 'target': target or '', 'config_file': path.name,
            'models': models, 'settings': settings,
            'status': _status_block(store),
            'masked': {name: True for name in TARGET_FIELDS}}


def _clamp_int(name, value, low, high):
    try:
        number = int(value)
    except Exception:
        return None, '%s=%r 不是整数，未写入' % (name, value)
    if number < low:
        return low, '%s=%s 低于下限，夹到 %s' % (name, number, low)
    if number > high:
        return high, '%s=%s 高于上限，夹到 %s' % (name, number, high)
    return number, ''


def _apply_model_patch(doc, target, patch, warned):
    block = doc.get(target) if isinstance(doc.get(target), dict) else {}
    allowed = TARGET_FIELDS.get(target, ())
    out = dict(block)
    for field, value in (patch or {}).items():
        if field not in allowed:
            warned.append('%s.%s 不是可写字段，已忽略' % (target, field))
            continue
        if field in ('timeout', 'top_n'):
            number, warn = _clamp_int('%s.%s' % (target, field), value,
                                      1 if field == 'timeout' else 1,
                                      60 if field == 'timeout' else 1000)
            if warn:
                warned.append(warn)
                if number is None:
                    continue
            out[field] = number
            continue
        if field == 'enabled':
            out[field] = bool(value)
            continue
        if field == 'key':
            # 面板存 key，落盘统一写成 key + api_key：两套读法都能看到新值
            text = str(value or '').strip()
            out['key'] = text
            out['api_key'] = text
            continue
        out[field] = str(value or '').strip()
    doc[target] = out
    return doc


def _clamp_float(name, value, low, high):
    try:
        number = float(value)
    except Exception:
        return None, '%s=%r 不是数字，未写入' % (name, value)
    if number < low:
        return low, '%s=%s 低于下限，夹到 %s' % (name, number, low)
    if number > high:
        return high, '%s=%s 高于上限，夹到 %s' % (name, number, high)
    return number, ''


def _apply_settings_patch(doc, patch, warned):
    recall = doc.get('recall') if isinstance(doc.get('recall'), dict) else {}
    for name, value in (patch or {}).items():
        if name not in SETTINGS_SPEC:
            warned.append('%s 不是可写设置，已忽略' % name)
            continue
        spec = SETTINGS_SPEC[name]
        if spec[0] == 'bool':
            doc[name] = bool(value)
            continue
        if spec[0] == 'str':
            allowed = tuple(spec[1])
            text = str(value if value is not None else '').strip().lower()
            if text not in allowed:
                warned.append('%s=%r 不在 %s 里，已忽略' % (name, value, '/'.join(allowed)))
                continue
            recall[name] = text
            continue
        if spec[0] == 'float':
            number, warn = _clamp_float(name, value, spec[1], spec[2])
        else:
            number, warn = _clamp_int(name, value, spec[1], spec[2])
        if warn:
            warned.append(warn)
        if number is not None:
            recall[name] = number
    if recall:
        doc['recall'] = recall
    return doc


def settings_set(store, target=None, patch=None, now=None):
    """写设置：备份 → 应用 → 原子写 → 读回校验。运行期立即生效（文件就是真源）。

    patch 可以同时带模型字段（target 那一段）与七个设置（顶层 / recall 段）。
    """
    path = _config_path(store)
    if path is None:
        return {'ok': False, 'code': 'no_store', 'detail': 'store 没有数据目录'}
    body = patch if isinstance(patch, dict) else {}
    model_patch = body.get('model') if isinstance(body.get('model'), dict) else {}
    settings_patch = body.get('settings') if isinstance(body.get('settings'), dict) else {}
    if target and target not in TARGET_FIELDS and model_patch:
        return {'ok': False, 'code': 'bad_target', 'detail': str(target)}
    if target and model_patch:
        target = str(target)
    # 空 patch：没有可应用的字段就不该"成功"，更不该留备份（没有改动就没有可回滚的东西）。
    recognised_model = [f for f in (model_patch or {})
                       if target and f in TARGET_FIELDS.get(target, ())]
    recognised_settings = [s for s in (settings_patch or {}) if s in SETTINGS_SPEC]
    recognised_extra = [k for k in ('expression', 'autonomy', 'sanitize_reply')
                        if k in (body.get('extra') if isinstance(body.get('extra'), dict) else {})]
    if not (recognised_model or recognised_settings or recognised_extra):
        return {'ok': False, 'code': 'empty_patch',
                'message': '没有可应用的字段（patch 为空或字段都不在白名单里）',
                'warned': []}
    warned = []
    current = _read_config(path)
    if not path.exists():
        return {'ok': False, 'code': 'missing_config',
                'detail': '没有 %s，先在面板里保存一次全局配置' % CONFIG_FILE}
    backup = path.with_name('%s.bak-%s' % (CONFIG_FILE,
                                           time.strftime('%Y-%m-%dT%H-%M-%S')))
    try:
        shutil.copyfile(path, backup)
    except Exception as exc:
        return {'ok': False, 'code': 'backup_failed',
                'detail': '%s: %s' % (type(exc).__name__, str(exc)[:80])}
    next_doc = json.loads(json.dumps(current))
    if model_patch and target:
        next_doc = _apply_model_patch(next_doc, target, model_patch, warned)
    if settings_patch:
        next_doc = _apply_settings_patch(next_doc, settings_patch, warned)
    # 旧面板还会写这三段（不属于模型与七个设置）：原样透传，仍是同一次写入。
    extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
    for key in ('expression', 'autonomy'):
        if isinstance(extra.get(key), dict):
            next_doc[key] = extra[key]
    if isinstance(extra.get('sanitize_reply'), bool):
        next_doc['sanitize_reply'] = extra['sanitize_reply']
    next_doc['updated_at'] = round(float(now if now is not None else time.time()), 3)
    try:
        from biocore_runtime import pipeline as P
        P._atomic_write(path, next_doc)
    except Exception as exc:
        return {'ok': False, 'code': 'write_failed',
                'detail': '%s: %s' % (type(exc).__name__, str(exc)[:80])}
    # 读回校验：磁盘上的值必须与本次写入一致（不做“写了就算”）
    disk = _read_config(path)
    mismatch = []
    if model_patch and target:
        block = disk.get(target) if isinstance(disk.get(target), dict) else {}
        for field, value in (next_doc.get(target) or {}).items():
            if block.get(field) != value:
                mismatch.append('%s.%s' % (target, field))
    for name in settings_patch:
        if name not in SETTINGS_SPEC:
            continue
        if SETTINGS_SPEC[name][0] == 'bool':
            if bool(disk.get(name)) != bool(next_doc.get(name)):
                mismatch.append(name)
        else:
            recall = disk.get('recall') if isinstance(disk.get('recall'), dict) else {}
            if recall.get(name) != (next_doc.get('recall') or {}).get(name):
                mismatch.append(name)
    if mismatch:
        return {'ok': False, 'code': 'verify_failed', 'backup': backup.name,
                'mismatch': mismatch[:6]}
    doc_out = {'ok': True, 'code': 'ok', 'backup': backup.name,
               'warned': warned[:8], 'target': target or ''}
    doc_out['models'] = {name: _masked_model(disk, name) for name in TARGET_FIELDS}
    recall = disk.get('recall') if isinstance(disk.get('recall'), dict) else {}
    doc_out['settings'] = {name: (bool(disk.get(name)) if SETTINGS_SPEC[name][0] == 'bool'
                                  else (recall.get(name) if name in recall else None))
                           for name in SETTINGS_SPEC}
    return doc_out
