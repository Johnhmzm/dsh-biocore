# -*- coding: utf-8 -*-
"""画像的落地：每会话一份 append-only 操作日志，视图是折叠出来的。

规则（总则：只增不改）：
  - 文件 `sessions/<sid>/portrait.json` 只追加操作（add / update），不删不改；
  - 读的时候用 `portrait.apply_ops` 折出当前视图；回滚靠历史操作重放，不靠改文件；
  - 内容（写什么话）来自总结层；这里只落盘与折视图，不调模型、不判断。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import portrait as _portrait

FILE_NAME = 'portrait.json'
SCHEMA_VERSION = 1


def path_for(session_dir):
    return Path(session_dir) / FILE_NAME


def load(session_dir):
    """读操作日志；没有文件返回空壳（不建文件）。"""
    path = path_for(session_dir)
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return {'schemaVersion': SCHEMA_VERSION, 'ops': []}
    if not isinstance(doc, dict):
        return {'schemaVersion': SCHEMA_VERSION, 'ops': []}
    ops = doc.get('ops')
    return {'schemaVersion': int(doc.get('schemaVersion') or SCHEMA_VERSION),
            'ops': list(ops) if isinstance(ops, list) else []}


def view(session_dir, now=None):
    """折叠成画像（供交付取最近 N 条）。"""
    state = _portrait.empty()
    for op in load(session_dir).get('ops') or []:
        if not isinstance(op, dict):
            continue
        state = _portrait.apply_ops(state, [op], now=op.get('at'))['portrait']
    return state


def append(session_dir, ops, at=None):
    """只追加：把这一批动作写进日志（独占创建；已存在则读+追加+整文件重写一次）。

    注意"整文件重写"只发生在**同一个日志文件**上，且内容 = 旧操作 + 新操作，
    旧操作一字不改；这是 append-only 语义在单文件 JSON 上的实现方式。
    """
    clean = [op for op in (ops or []) if isinstance(op, dict)]
    if not clean:
        return 0
    stamp = float(at if at is not None else time.time())
    path = path_for(session_dir)
    doc = load(session_dir)
    doc['ops'].extend(dict(op, at=stamp) for op in clean)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    return len(clean)
