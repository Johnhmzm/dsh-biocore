# -*- coding: utf-8 -*-
"""记忆脱敏：进模型的文本里把手机号 / 身份证 / 邮箱 / 门牌式地址打码，原文留在库里。

三条边界（和 docs/LAYER-BOUNDARY.md 一致）：
  1. **只改解释层交付出去的那一段**。归档里的原文一个字节不动（`entries`/`events`
     照旧是原文），脱敏发生在"要念给模型听"的那一步。
  2. **可逆但不明文落盘**：原文另存一份映射，映射里存的是密文（HMAC-SHA256 计数器
     流 + 完整性标签），密钥单独一个文件、权限 0600、和映射不在同一个目录。
     这是"可逆"，不是"不可逆哈希"——要能还原回去才谈得上不丢信息。
  3. **默认关闭**。`BIOCORE_REDACT` 没开时 `mask_block()` 原样返回入参，
     逐字节一致（本模块不 import 任何会改状态的东西）。

强度声明（必须写在明面上）：这里的"加密"是自己拼的计数器流，**没有经过密码学评审**，
只能保证"目录里不出现明文"，挡不住拿到密钥的人。要抵抗真实攻击，得换 AES-GCM 或
字段级 KMS——那需要新依赖，本机不允许联网装包，所以这一步留给你决定。

用法（运行时）：
    BIOCORE_REDACT=1  # 打开注入前脱敏
    from biocore_runtime import redact
    redact.mask_block(store, session_id, text)   # 关着时是恒等函数
    redact.restore_text(store, session_id, text) # 用落盘的映射把打码还原
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time

ENV = 'BIOCORE_REDACT'
MAP_DIR = '.redact'
KEY_FILE = 'key.bin'
MAP_FILE = 'map.jsonl'
KEY_BYTES = 32
YES = ('1', 'true', 'on', 'yes')

# 顺序有意义：18 位身份证先跑，否则它内部可能被手机号规则咬掉一段。
# 打码都保留长度与首尾，读起来仍是"一个号码"，只是中间没了。
RULES = (
    {
        'name': 'id_card_18',
        'pattern': re.compile(r'(?<!\d)(\d{6})(\d{8})(\d{3}[0-9Xx])(?!\d)'),
        'mask': lambda m: m.group(1) + '*' * 8 + m.group(3),
    },
    {
        'name': 'id_card_15',
        'pattern': re.compile(r'(?<!\d)(\d{6})(\d{6})(\d{3})(?!\d)'),
        'mask': lambda m: m.group(1) + '*' * 6 + m.group(3),
    },
    {
        'name': 'phone',
        'pattern': re.compile(r'(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)'),
        'mask': lambda m: m.group(1) + '*' * 4 + m.group(3),
    },
    {
        'name': 'email',
        'pattern': re.compile(r'([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*'
                              r'@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})'),
        'mask': lambda m: m.group(1) + '***@' + m.group(2),
    },
    # 门牌式地址：路名保留（不然句子读不通），门牌号打掉。
    {
        'name': 'addr_road_no',
        'pattern': re.compile(r'([\u4e00-\u9fff]{1,10}(?:路|街|道|巷|大道|胡同))'
                              r'\s*(\d{1,4})\s*(号)'),
        'mask': lambda m: m.group(1) + '**' + m.group(3),
    },
    {
        'name': 'addr_building',
        'pattern': re.compile(r'(\d{1,4})\s*(号楼|栋|幢|单元)'),
        'mask': lambda m: '**' + m.group(2),
    },
    {
        'name': 'addr_room',
        'pattern': re.compile(r'(\d{1,4})\s*(室|号房)'),
        'mask': lambda m: '**' + m.group(2),
    },
)


def enabled(value=None) -> bool:
    """开关：显式传值 > 环境变量 > 关。"""
    if isinstance(value, bool):
        return value
    raw = os.environ.get(ENV) if value is None else value
    return str(raw or '').strip().lower() in YES


# ------------------------------------------------------------------ 打码 / 还原
def find_hits(text):
    """按规则顺序找出所有命中：[(rule, masked, start, end)]（不重叠）。"""
    body = str(text or '')
    if not body:
        return []
    taken = []
    hits = []
    for rule in RULES:
        for match in rule['pattern'].finditer(body):
            start, end = match.span()
            if any(not (end <= lo or start >= hi) for lo, hi in taken):
                continue
            masked = rule['mask'](match)
            if masked == match.group(0):
                continue
            taken.append((start, end))
            hits.append((rule['name'], masked, start, end, match.group(0)))
    hits.sort(key=lambda row: row[2])
    return hits


def mask_text(text):
    """返回 (打码后的文本, 命中列表)。命中列表里带原文，调用方负责别把它落明文。"""
    body = str(text or '')
    hits = find_hits(body)
    if not hits:
        return body, []
    out = []
    cursor = 0
    for _name, masked, start, end, _raw in hits:
        out.append(body[cursor:start])
        out.append(masked)
        cursor = end
    out.append(body[cursor:])
    return ''.join(out), hits


def restore_text(masked, originals):
    """用命中列表把打码还原：按规则顺序、第 k 次命中换回第 k 个原文。

    `originals` 可以是 find_hits() 的结果，也可以是 load_mapping() 读回来的行。
    """
    body = str(masked or '')
    rows = [row for row in (originals or []) if row]
    if not body or not rows:
        return body
    by_rule = {}
    for row in rows:
        if isinstance(row, dict):
            name, masked_form, raw = row.get('rule'), row.get('masked'), row.get('raw')
        else:
            name, masked_form, raw = row[0], row[1], row[4] if len(row) > 4 else None
        if raw is None:
            continue
        by_rule.setdefault(str(name), []).append((str(masked_form or ''), str(raw)))
    out = body
    for rule in RULES:
        pairs = by_rule.get(rule['name']) or []
        if not pairs:
            continue
        for masked_form, raw in pairs:
            if masked_form and masked_form in out:
                out = out.replace(masked_form, raw, 1)
    return out


# ------------------------------------------------------------------ 映射的落盘
def _root(store) -> str:
    return os.path.join(str(store), MAP_DIR)


def _map_path(store, session_id) -> str:
    safe = re.sub(r'[^0-9A-Za-z_.-]', '_', str(session_id or 'default'))[:60]
    return os.path.join(_root(store), safe, MAP_FILE)


def _key_path(store) -> str:
    return os.path.join(_root(store), KEY_FILE)


def _load_key(store):
    """读密钥；没有就生成。权限 0600，且**不在**会话子目录里（不和密文放一起）。"""
    path = _key_path(store)
    try:
        with open(path, 'rb') as fh:
            key = fh.read()
        if len(key) == KEY_BYTES:
            return key
    except Exception:
        pass
    key = secrets.token_bytes(KEY_BYTES)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(key)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        return b''
    return key


def _keystream(key, nonce, size):
    out = b''
    counter = 0
    while len(out) < size:
        out += hmac.new(key, nonce + counter.to_bytes(4, 'big'),
                        hashlib.sha256).digest()
        counter += 1
    return out[:size]


def seal(key, raw):
    """把原文封成 (nonce_hex, blob_hex)：计数器流异或 + HMAC 完整性标签。"""
    if not key:
        return '', ''
    data = str(raw or '').encode('utf-8')
    nonce = secrets.token_bytes(8)
    stream = _keystream(key, nonce, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    return nonce.hex(), (cipher + tag).hex()


def unseal(key, nonce_hex, blob_hex):
    """还原 seal() 的结果；被改过就返回空串（不做"尽量还原"）。"""
    if not key or not nonce_hex or not blob_hex:
        return ''
    try:
        nonce = bytes.fromhex(str(nonce_hex))
        blob = bytes.fromhex(str(blob_hex))
        cipher, tag = blob[:-16], blob[-16:]
        want = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, want):
            return ''
        stream = _keystream(key, nonce, len(cipher))
        data = bytes(a ^ b for a, b in zip(cipher, stream))
        return data.decode('utf-8')
    except Exception:
        return ''


def save_mapping(store, session_id, hits, now=None):
    """把一次打码的原文封进映射（密文）。返回写入行数。"""
    rows = [row for row in (hits or []) if row]
    if not rows:
        return 0
    key = _load_key(store)
    if not key:
        return 0
    path = _map_path(store, session_id)
    stamp = float(now if now is not None else time.time())
    written = 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            for name, masked, _start, _end, raw in rows:
                nonce, blob = seal(key, raw)
                if not blob:
                    continue
                fh.write(json.dumps({
                    'rule': str(name), 'masked': str(masked),
                    'nonce': nonce, 'blob': blob, 'ts': round(stamp, 3)},
                    ensure_ascii=False) + chr(10))
                written += 1
    except Exception:
        return 0
    return written


def load_mapping(store, session_id):
    """读回映射并解密：[{rule, masked, raw}]。解不开的行跳过（不猜）。"""
    path = _map_path(store, session_id)
    key = _load_key(store)
    rows = []
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except Exception:
                    continue
                raw = unseal(key, doc.get('nonce'), doc.get('blob'))
                if not raw:
                    continue
                rows.append({'rule': doc.get('rule'), 'masked': doc.get('masked'),
                             'raw': raw})
    except Exception:
        return []
    return rows


def mask_block(store, session_id, text, now=None, record=True):
    """交付口用的一个函数：关着时原样返回；开着时打码 + 存可逆映射。

    返回 (text, hits)。hits 里的原文只用于即时还原，不要写进日志。
    """
    body = str(text or '')
    if not body or not enabled():
        return body, []
    masked, hits = mask_text(body)
    if not hits:
        return body, []
    if record:
        save_mapping(store, session_id, hits, now=now)
    return masked, hits


def restore_from_store(store, session_id, masked):
    """用落盘的映射把一段打码文本还原回原文。"""
    return restore_text(masked, load_mapping(store, session_id))
