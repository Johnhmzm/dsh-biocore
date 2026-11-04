"""Turn raw vision pipeline output into a human-facing image context.

The recognizer's labels/confidences are evidence for the model, not something
the character should discuss with the user.
"""
from __future__ import annotations

import re


def clean_vision_text(text: str) -> str:
    s = str(text or "")
    if not s.strip():
        return ""
    s = s.replace("【画面内容】", "")
    # drop the uncertain half, which is often self-contradictory
    s = re.split(r"；?\s*疑似(?:角色|标签)[^：:]*[:：]?", s, maxsplit=1)[0]
    s = re.sub(r"确定(?:标签|角色)[:：]", "", s)
    s = re.sub(r"角色[:：]", "", s)
    s = re.sub(r"疑似(?:标签|角色)[^：:]*[:：]", "", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "", s)
    s = s.replace("需结合下方描述判断是否成立", "").replace("低置信", "")
    s = s.replace("、", "、").replace("，", "，")
    s = re.sub(r"[、,，;；\s]+", "、", s).strip("、 ")
    if not s:
        return ""
    return "画面里能看到：" + s
