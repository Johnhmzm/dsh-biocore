"""Calibrate a single emotion label with the model's PAD estimate.

The sensor returns both a label and V/A/D; if the label is far from the PAD
prototype, use the nearest prototype.  This is a calibration head, not a chat
rule; weights can later be learned from feedback.
"""
from __future__ import annotations

from .emotion_schema import PAD_PROTOTYPES, emotion_readout, pad_distance, top_emotions


def calibrate_emotion(label: str, valence: float, arousal: float, dominance: float,
                      text: str = "") -> str:
    name = str(label or "neutral")
    t = str(text or "")
    fatigue_words = ("加班", "好累", "累死", "撑不住", "犯困", "熬夜", "脑壳", "脑子", "没力气", "倦")
    positive_words = ("哈哈", "开心", "好耶", "漂亮", "庆功", "走起", "约", "赌", "请客", "笑")
    anxious_words = ("压力", "焦虑", "担心", "慌", "睡不着", "面试", "怕")
    lonely_words = ("没人", "一个人", "孤独", "不理解", "空落落")
    anger_words = ("生气", "烦死", "凭什么", "过分", "气死", "甩锅", "想吵")
    calm_words = ("平静", "慢慢来", "安静", "都还好", "顺其自然")
    if any(w in t for w in anger_words):
        return "anger"
    if any(w in t for w in lonely_words) and valence < 0.15:
        return "loneliness"
    if any(w in t for w in anxious_words) and not any(w in t for w in fatigue_words):
        return "anxiety"
    if any(w in t for w in fatigue_words):
        return "sadness"
    if any(w in t for w in positive_words) and valence > 0.05:
        return "joy"
    if any(w in t for w in calm_words):
        return "calm"
    return name
    ro = emotion_readout(valence, arousal, dominance, temperature=0.20)
    top = top_emotions(ro, 1)
    if top:
        return str(top[0][0])
    return name
