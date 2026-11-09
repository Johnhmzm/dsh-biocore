from __future__ import annotations

from typing import Dict

from .persona import Persona

# prompt -> trait evidence; this is a compiler, not a runtime rule
LEX = {
    "E": ["亲近", "活泼", "俏皮", "主动", "热情", "话多", "爱聊", "开朗", "playful", "warm", "outgoing"],
    "A": ["温暖", "关心", "耐心", "体贴", "先接情绪", "不否定", "温柔", "kind", "caring"],
    "O": ["好奇", "想象", "新鲜", "探索", "创意", "有趣", "curious", "creative"],
    "N": ["焦虑", "敏感", "易紧张", "不安", "anxious", "sensitive"],
    "C": ["严谨", "正式", "规则", "克制", "专业", "formal", "strict"],
}


def compile_persona(prompt: str, seed: int = 0) -> Persona:
    text = str(prompt or "").lower()
    profile: Dict[str, float] = {"O": 0.5, "C": 0.5, "E": 0.5, "A": 0.5, "N": 0.5}
    for trait, words in LEX.items():
        hits = sum(1 for w in words if w.lower() in text)
        delta = min(0.30, 0.10 * hits)
        if trait == "N":
            profile[trait] = max(0.05, 0.45 - delta)  # negative words are rare in persona prompts
        else:
            profile[trait] = min(0.95, 0.45 + delta)
    # explicit "不/别" negations flip some traits
    if any(x in text for x in ("不主动", "冷淡", "话少")):
        profile["E"] = max(0.05, profile["E"] - 0.25)
    if any(x in text for x in ("不温暖", "不关心", "毒舌")):
        profile["A"] = max(0.05, profile["A"] - 0.20)
    persona = Persona.from_profile(profile, seed=seed)
    persona.refresh_numbers()
    return persona
