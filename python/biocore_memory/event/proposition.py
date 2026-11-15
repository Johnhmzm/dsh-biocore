# -*- coding: utf-8 -*-
"""命题（第二层）：把一句原文改写成一条自足的陈述，落在 units.gist。

为什么要有这一层：原文一句常常缺主语（"改到周六了"），逐句交付时读者（模型）
得自己回填；命题把主语、时间、数字都补齐并且**不许多出原文没有的事实**，
检索与交付都用得上，原文永远还在（entries/units.text 不动）。

四条口径（与设计一致）：
  1. 20–35 字为目标，信息完整优先，可超；超过 50 字算太长（只提示，不拦）。
  2. 主语必须出现：说话人自己写「我」，对方写**全名**——名字运行时注入
     （names 参数），不写死在代码里。
  3. 禁代词：你 / 他 / 她 / 它 / 其 / 该。
  4. 数字、编号、路径、文件名、时间原样保留；原文太短或含糊允许扩写，
     补不出来写「未明确」，不许编。

边界（与 SEVEN-STEPS 一致）：本模块**只构造提示词与校验文本**，不自己调模型——
调用进来的是一个 summarizer 可调用对象（和沉一样），所以测试里能塞 stub。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 目标区间与硬上限：只用于校验/提示，不拦写（信息完整优先）
TARGET_MIN = 20
TARGET_MAX = 35
HARD_MAX = 50
# 禁用的代词（「我」不在内：自己就是要写「我」）
BANNED_PRONOUNS = ('你', '他', '她', '它', '其', '该')
PROMPT_VERSION = 'gist-v3'  # 改提示词必须同步改这里，否则缓存会返回旧写法
UNKNOWN = '未明确'

RULES = """你是句子改写器：把原文改写成一条自足的命题（一句话）。
1. 长度 20–35 字为目标；信息完整优先，可以超一点，但不要超过 50 字。
2. 主语必须出现：说话人自己一律写「我」。
   对方只在原文确实提到时才写全名（可用名字：{names}）；原文没提到对方，就不要写对方的名字。
3. 禁止出现代词：你 / 他 / 她 / 它 / 其 / 该。
4. 数字、编号、路径、文件名、时间原样保留，不换算、不省略、不改写。
5. 原文可能是被切开的半句（句子碎片）：这时只把已给出的内容写成命题，
   不要猜它前后是什么，也不要补出一个原文里没有的人名或数值。
6. 「未明确」只在主语确实缺失时用一次；能用原文里的东西说清楚的，一律写清楚，不许拿它凑字数。
7. 原文太短或含糊时，可以按上下文扩写，但补出来的必须是原文里已有的信息。
   不要写「我指出原文中」「我确认原文里」这类元叙述——直接陈述事实本身。
8. 只输出这一条命题本身：不要解释、不要引号、不要编号、不要换行。
"""


def names_text(names=None):
    """把运行时注入的名字列表变成提示词里的一段（空列表就明说没有）。"""
    clean = []
    for name in (names or []):
        text = str(name or '').strip()
        if text and text not in clean:
            clean.append(text)
    return '、'.join(clean) if clean else '（本次没有可用姓名，对方主体写「未明确」）'


def build_prompt(text, names=None):
    """一条命题的提示词；原文为空返回空串（调用方据此跳过）。"""
    body = str(text or '').strip()
    if not body:
        return ''
    return RULES.format(names=names_text(names)) + '\n原文：' + body


def clean(value):
    """去掉模型爱加的引号/编号/前后空白；不改写正文。"""
    text = str(value or '').strip()
    for quote in ('“”', '"', '「」', "'", '‘’'):
        if len(text) >= 2 and text[0] in quote and text[-1] in quote:
            text = text[1:-1].strip()
    while text[:1] in ('-', '·', '1') and text[1:2] in ('.', '、', ' '):
        text = text[2:].strip()
    # 模型爱在开头加省略号/破折号（"……我在周五……"）：命题不该以它们开头
    while text[:1] in ('…', '—', '–', '\u3000', ' '):
        text = text[1:].lstrip()
    return text.strip()


def check(gist, names=None):
    """校验一条命题，返回问题码列表（空 = 通过）。

    只是**记录**问题，不拦写：信息完整优先，宁可留一条 18 字的命题，
    也不要为了凑字数把事实改坏。
    """
    text = str(gist or '').strip()
    problems = []
    if not text:
        return ['empty']
    if len(text) < TARGET_MIN:
        problems.append('short')
    if len(text) > HARD_MAX:
        problems.append('long')
    hits = [word for word in BANNED_PRONOUNS if word in text]
    if hits:
        problems.append('pronoun:' + ''.join(hits))
    has_subject = '我' in text or any(
        str(name or '').strip() and str(name).strip() in text for name in (names or []))
    if not has_subject:
        problems.append('no_subject')
    if UNKNOWN in text:
        problems.append('unknown_filled')
    return problems


def generate(text, summarizer, names=None, cache_dir=None, model=''):
    """调一次摘要器产出一条命题。

    返回 {'gist': str, 'cached': bool, 'ms': float, 'problems': [..]}；
    没有摘要器/原文为空 -> {'gist': '', 'reason': ...}，绝不抛。
    """
    prompt = build_prompt(text, names)
    if not prompt:
        return {'gist': '', 'reason': 'empty_text', 'cached': False, 'ms': 0.0}
    if summarizer is None:
        return {'gist': '', 'reason': 'no_summarizer', 'cached': False, 'ms': 0.0}
    try:
        if cache_dir is None:
            answer, cached, ms = summarizer(prompt), False, 0.0
        else:
            from biocore_memory.store import summary_cache as _cache
            got = _cache.call(summarizer, prompt, cache_dir, model,
                              version=PROMPT_VERSION,
                              content='%s|%s\n%s' % (PROMPT_VERSION,
                                                     names_text(names),
                                                     str(text or '').strip()))
            answer, cached, ms = got.get('text'), bool(got.get('cached')), float(got.get('ms') or 0.0)
    except Exception as exc:
        logger.warning('[gist] 生成失败：%s: %s', type(exc).__name__, str(exc)[:120])
        return {'gist': '', 'reason': 'error:%s' % type(exc).__name__,
                'cached': False, 'ms': 0.0}
    gist = clean(answer)
    return {'gist': gist, 'cached': cached, 'ms': ms,
            'problems': check(gist, names) if gist else ['empty']}
