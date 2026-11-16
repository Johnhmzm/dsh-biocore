# -*- coding: utf-8 -*-
"""摘要调用的缓存：内容指纹 + 模型名 + 提示词版本，命中就不发请求。

规则（2026-11-13 定）：
  - 键 = sha256(内容) + 模型名 + 提示词版本；三者任一变化才重算。
  - 位置在 store 的 `cache/` 目录（可弃），不进记忆本体；缓存文件**只增不改**
    （写用独占创建，已存在就读，绝不覆盖）。
  - 不存密钥、不存提示词原文：文件里只有指纹、模型名、结果文本与时间。
  - 不跨模型混用：模型名进文件名，换模型自然不命中；旧文件保留不删。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

PROMPT_VERSION = 'consolidate-v1'
CACHE_SUBDIR = ('cache', 'summary')


def fingerprint(text):
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def _slug(value):
    """模型名进文件名：只留安全字符，避免路径穿越与空格问题。"""
    out = ''.join(ch if (ch.isalnum() or ch in '-_.') else '_' for ch in str(value or 'unknown'))
    return out[:48] or 'unknown'


def cache_path(cache_dir, content, model, version=PROMPT_VERSION):
    return Path(cache_dir) / ('%s-%s-%s.json' % (_slug(version), _slug(model),
                                                 fingerprint(content)[:32]))


def read(cache_dir, content, model, version=PROMPT_VERSION):
    """命中返回 {text, fingerprint, model, version}；未命中返回 None。"""
    path = cache_path(cache_dir, content, model, version)
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(doc, dict) or doc.get('model') != _slug(model):
        return None
    if str(doc.get('fingerprint') or '') != fingerprint(content)[:32]:
        return None
    return doc


def write(cache_dir, content, model, text, version=PROMPT_VERSION, ms=0.0):
    """只增不改：文件已存在就不覆盖；返回路径。"""
    path = cache_path(cache_dir, content, model, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'version': str(version), 'model': _slug(model),
               'fingerprint': fingerprint(content)[:32],
               'text': str(text or ''), 'ms': round(float(ms), 1),
               'created_at': round(time.time(), 3)}
    # 只增不改 + 原子：先写临时文件再换名。旧写法用 'x' 直接写目标，进程在 dump
    # 中途死掉会留下残缺 JSON，之后 read() 永远未命中、'x' 又永远不再覆盖它。
    tmp = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
    try:
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            tmp.unlink()
        else:
            os.replace(str(tmp), str(path))
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def call(summarizer, prompt, cache_dir, model, version=PROMPT_VERSION, content=None):
    """带缓存的摘要调用：命中不发请求；返回 {text, cached, ms}。"""
    material = content if content is not None else prompt
    t0 = time.time()
    hit = read(cache_dir, material, model, version)
    if hit is not None:
        return {'text': str(hit.get('text') or ''), 'cached': True,
                'ms': round((time.time() - t0) * 1000, 1)}
    text = summarizer(prompt)
    spent = round((time.time() - t0) * 1000, 1)
    write(cache_dir, material, model, text, version=version, ms=spent)
    return {'text': str(text or ''), 'cached': False, 'ms': spent}
