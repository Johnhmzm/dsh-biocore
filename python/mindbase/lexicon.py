from __future__ import annotations

# Tiny demo lexicon only. For real calibration replace this with
# ANEW / NRC-VAD / Warriner (or a Chinese/Japanese norm table).
# Values are (valence in [-1, 1], arousal in [0, 1]).
_WORDS = {
    "好": (0.6, 0.5), "棒": (0.7, 0.6), "喜欢": (0.8, 0.6), "爱": (0.8, 0.6),
    "开心": (0.7, 0.6), "谢谢": (0.6, 0.5), "厉害": (0.7, 0.6), "可爱": (0.7, 0.6),
    "坏": (-0.6, 0.6), "讨厌": (-0.7, 0.6), "烦": (-0.5, 0.5), "滚": (-0.8, 0.8),
    "废物": (-0.8, 0.7), "难过": (-0.7, 0.4), "累": (-0.4, 0.4), "怕": (-0.6, 0.7),
    "气": (-0.6, 0.7), "hello": (0.2, 0.4), "thanks": (0.6, 0.5),
    "love": (0.8, 0.6), "hate": (-0.8, 0.7), "sad": (-0.7, 0.4),
}


def text_affect(text: str) -> tuple[float, float]:
    """Very small lookup. Returns (valence, arousal); neutral by default."""
    if not text:
        return 0.0, 0.5
    vs, ars, n = 0.0, 0.0, 0
    low = text.lower()
    for word, (v, a) in _WORDS.items():
        if word in low:
            vs += v
            ars += a
            n += 1
    if n == 0:
        return 0.0, 0.5
    return clip(vs / n, -1.0, 1.0), clip(ars / n, 0.0, 1.0)


def clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x
