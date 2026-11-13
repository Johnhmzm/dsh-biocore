# -*- coding: utf-8 -*-
"""请求作用域：一次交付只读一遍库，后续环节共用同一份。

为什么不是全局缓存：全局缓存分不清"同一个请求"和"下一次请求"，一个写操作就能让
它变成脏数据；显式传下去的作用域在请求结束就消失，语义与生命周期都跟着请求走。

约定：作用域里的文档按**只读**对待。需要改内容的写路径（capture / 事件维护 /
claim 实体回写）必须自己 load_memory，不许拿作用域里的对象去改。
"""
from __future__ import annotations


class RequestScope(object):
    """一次请求的只读文档作用域；同一 key 只读一次，之后返回同一对象。"""

    def __init__(self):
        self._docs = {}
        self.loads = 0          # 真实读库次数（测试与性能报告用）

    def load(self, key, loader):
        if key in self._docs:
            return self._docs[key]
        doc = loader()
        self._docs[key] = doc
        self.loads += 1
        return doc

    def get(self, key, default=None):
        return self._docs.get(key, default)

    def clear(self):
        self._docs.clear()
