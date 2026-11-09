from __future__ import annotations

import copy
import json
import math
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence

from .noise import clip
from .social import Bond


def _kc_set(mb, cue: str) -> set:
    return set(mb.mb.kc.encode(cue))


def kc_sparsity(mb) -> float:
    if mb.mb.kc.n <= 0:
        return 0.0
    return len(_kc_set(mb, "__eval_probe__")) / float(mb.mb.kc.n)


def weight_stats(mb) -> Dict[str, float]:
    all_weights = list(mb.mb.mbon_reward.w) + list(mb.mb.mbon_punishment.w)
    return {
        "reward_sum": round(sum(mb.mb.mbon_reward.w), 4),
        "punishment_sum": round(sum(mb.mb.mbon_punishment.w), 4),
        "max_abs": round(max((abs(w) for w in all_weights), default=0.0), 4),
        "total_abs": round(sum(abs(w) for w in all_weights), 4),
    }


def behavior_entropy(actions: Sequence[str]) -> float:
    if not actions:
        return 0.0
    counts = Counter(actions)
    total = float(len(actions))
    h = 0.0
    for n in counts.values():
        p = n / total
        h -= p * math.log2(p)
    return round(h, 4)


def expected_calibration_error(confidences: Sequence[float], correct: Sequence[bool], bins: int = 5) -> Optional[float]:
    if not confidences or len(confidences) != len(correct):
        return None
    buckets: List[List[tuple]] = [[] for _ in range(bins)]
    for conf, ok in zip(confidences, correct):
        c = clip(float(conf), 0.0, 1.0)
        idx = min(bins - 1, int(c * bins))
        buckets[idx].append((c, bool(ok)))
    total = float(len(confidences))
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        avg_conf = sum(c for c, _ in bucket) / len(bucket)
        acc = sum(1.0 for _, ok in bucket if ok) / len(bucket)
        ece += (len(bucket) / total) * abs(avg_conf - acc)
    return round(ece, 4)


def kc_separation(mb, cues: Sequence[str]) -> float:
    sets = [_kc_set(mb, c) for c in cues if c]
    if len(sets) < 2:
        return 0.0
    vals = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            if union:
                vals.append(len(sets[i] & sets[j]) / len(union))
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def channel_correlation(mb, cues: Sequence[str]) -> Optional[float]:
    rs, ps = [], []
    for c in cues:
        ch = mb.mb.predict_channels(c)
        rs.append(float(ch["reward"]))
        ps.append(float(ch["punishment"]))
    if len(rs) < 2:
        return None
    mr, mp = sum(rs) / len(rs), sum(ps) / len(ps)
    num = sum((r - mr) * (p - mp) for r, p in zip(rs, ps))
    dr = math.sqrt(sum((r - mr) ** 2 for r in rs))
    dp = math.sqrt(sum((p - mp) ** 2 for p in ps))
    if dr == 0 or dp == 0:
        return 0.0
    return round(num / (dr * dp), 4)


def ood_gap(mb, trained_cues: Sequence[str], novel_cues: Sequence[str]) -> float:
    def mean_value(cues):
        vals = [float(mb.mb.predict_channels(c)["value"]) for c in cues if c]
        return sum(vals) / len(vals) if vals else 0.0

    return round(mean_value(trained_cues) - mean_value(novel_cues), 4)


def _one_hot_kc(mb, cue: str) -> List[float]:
    vec = [0.0] * mb.mb.kc.n
    for i in mb.mb.kc.encode(cue):
        vec[i] = 1.0
    return vec


def kc_rdm(mb, cues: Sequence[str]) -> List[List[float]]:
    """Representational dissimilarity matrix (1 - Jaccard) over KC codes."""
    n = len(cues)
    rdm = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = _kc_set(mb, cues[i]), _kc_set(mb, cues[j])
            union = a | b
            d = 1.0 - (len(a & b) / len(union) if union else 0.0)
            rdm[i][j] = rdm[j][i] = round(d, 4)
    return rdm


def _upper(rdm: List[List[float]]) -> List[float]:
    n = len(rdm)
    return [rdm[i][j] for i in range(n) for j in range(i + 1, n)]


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def rsa_similarity(mb_a, mb_b, cues: Sequence[str]) -> Optional[float]:
    """RSA: correlation between two KC RDMs (1.0 = same geometry)."""
    return round(_pearson(_upper(kc_rdm(mb_a, cues)), _upper(kc_rdm(mb_b, cues))), 4)


def _gram(matrix: List[List[float]]) -> List[List[float]]:
    return [
        [sum(a * b for a, b in zip(matrix[i], matrix[j])) for j in range(len(matrix))]
        for i in range(len(matrix))
    ]


def linear_cka(mb_a, mb_b, cues: Sequence[str]) -> float:
    """Linear CKA between KC representations of two bases."""
    if len(cues) < 2:
        return 0.0
    x = [_one_hot_kc(mb_a, c) for c in cues]
    y = [_one_hot_kc(mb_b, c) for c in cues]
    n, d = len(x), len(x[0])

    def center(matrix):
        means = [sum(row[j] for row in matrix) / n for j in range(d)]
        return [[row[j] - means[j] for j in range(d)] for row in matrix]

    x, y = center(x), center(y)
    k, l = _gram(x), _gram(y)
    hsic = sum(k[i][j] * l[i][j] for i in range(n) for j in range(n))
    kk = sum(v * v for row in k for v in row)
    ll = sum(v * v for row in l for v in row)
    if kk == 0 or ll == 0:
        return 0.0
    return round(hsic / math.sqrt(kk * ll), 4)


def _lesion(mb, name: str):
    ab = copy.deepcopy(mb)
    if name == "reward":
        ab.mb.mbon_reward.w = [0.0] * ab.mb.kc.n
    elif name == "punishment":
        ab.mb.mbon_punishment.w = [0.0] * ab.mb.kc.n
    elif name == "mb":
        ab.mb.mbon_reward.w = [0.0] * ab.mb.kc.n
        ab.mb.mbon_punishment.w = [0.0] * ab.mb.kc.n
        ab.mb.lr = 0.0
    elif name == "apl":
        ab.mb.apl.strength = 0.0
        ab.mb.apl.activity = 0.0
    elif name == "cross":
        ab.mb.punish_cross_lr = 0.0
    else:
        raise ValueError(f"unknown lesion: {name}")
    return ab


def lesion_effect(mb, cue: str, feedback: float = 1.0, lesion: str = "reward", learn: bool = False) -> float:
    """How much current behavior depends on one module.

    learn=False: lesion existing weights and compare predictions (default).
    learn=True:  let both bases learn the feedback, then compare (learning effect).
    """
    intact = copy.deepcopy(mb)
    ablated = _lesion(mb, lesion)
    if learn:
        intact.post_reply(cue, feedback=feedback)
        ablated.post_reply(cue, feedback=feedback)
    vi = float(intact.mb.predict_channels(cue)["value"])
    va = float(ablated.mb.predict_channels(cue)["value"])
    return round(abs(vi - va), 4)


def ablation_report(mb, cue: str, feedback: float = 1.0, learn: bool = False) -> Dict[str, float]:
    return {
        name: lesion_effect(mb, cue, feedback, name, learn=learn)
        for name in ("reward", "punishment", "mb", "apl", "cross")
    }


def ood_suite(mb, trained_cues: Sequence[str], novel_cues: Sequence[str]) -> Optional[Dict[str, float]]:
    def vals(cues):
        return [float(mb.mb.predict_channels(c)["value"]) for c in cues if c]

    t, n = vals(trained_cues), vals(novel_cues)
    if not t or not n:
        return None
    wins = sum(1 for a in t for b in n if a > b)
    ties = sum(1 for a in t for b in n if a == b)
    auroc = (wins + 0.5 * ties) / (len(t) * len(n))
    return {
        "trained_mean": round(sum(t) / len(t), 4),
        "novel_mean": round(sum(n) / len(n), 4),
        "gap": round(sum(t) / len(t) - sum(n) / len(n), 4),
        "auroc": round(auroc, 4),
    }


def _apply_learning_lesion(mb, name: str):
    if name == "no_learning":
        mb.mb.lr = 0.0
    elif name == "no_cross":
        mb.mb.punish_cross_lr = 0.0
    elif name == "no_apl":
        mb.mb.apl.strength = 0.0
        mb.mb.apl.activity = 0.0
    elif name == "no_trace":
        mb.learner.use_trace = False
    elif name == "no_admission":
        mb.learner.min_feedback = 0.0
        mb.learner.min_rpe = 0.0
    else:
        raise ValueError(f"unknown learning lesion: {name}")
    return mb


def _train_cues(mb, cues: Sequence[str], feedback: float) -> None:
    for cue in cues:
        mb.post_reply(cue, feedback=feedback)


def _mean_value(mb, cues: Sequence[str]) -> float:
    vals = [float(mb.mb.predict_channels(c)["value"]) for c in cues if c]
    return sum(vals) / len(vals) if vals else 0.0


def learning_ablation_report(
    mb,
    train_cues: Sequence[str],
    probe_cues: Sequence[str],
    feedback: float = 1.0,
) -> Dict[str, float]:
    """Ablate the learning mechanism, train, then compare probe values."""
    intact = copy.deepcopy(mb)
    _train_cues(intact, train_cues, feedback)
    base = _mean_value(intact, probe_cues)
    report: Dict[str, float] = {}
    for name in ("no_learning", "no_cross", "no_apl", "no_trace", "no_admission"):
        ablated = copy.deepcopy(mb)
        _apply_learning_lesion(ablated, name)
        _train_cues(ablated, train_cues, feedback)
        report[name] = round(abs(base - _mean_value(ablated, probe_cues)), 4)
    return report


def speaker_ood(mb, known_speakers: Sequence[str], novel_speakers: Sequence[str]) -> Optional[Dict[str, float]]:
    if not known_speakers and not novel_speakers:
        return None
    default = Bond()

    def stats(names):
        bonds = [mb.social.bonds.get(n, default) for n in names] or [default]
        return {
            "familiarity": sum(b.familiarity for b in bonds) / len(bonds),
            "trust": sum(b.trust for b in bonds) / len(bonds),
            "attachment": sum(b.attachment for b in bonds) / len(bonds),
        }

    k, n = stats(known_speakers), stats(novel_speakers)
    return {
        "known_familiarity": round(k["familiarity"], 4),
        "novel_familiarity": round(n["familiarity"], 4),
        "familiarity_gap": round(k["familiarity"] - n["familiarity"], 4),
        "known_trust": round(k["trust"], 4),
        "novel_trust": round(n["trust"], 4),
        "trust_gap": round(k["trust"] - n["trust"], 4),
    }


def _mean_channel(mb, cues: Sequence[str], channel: str) -> float:
    vals = [float(mb.mb.predict_channels(c)[channel]) for c in cues if c]
    return sum(vals) / len(vals) if vals else 0.0


def _apl_gain_for(mb, cue: str) -> float:
    apl = copy.deepcopy(mb.mb.apl)
    kc = mb.mb.kc.encode(cue)
    return round(apl.step(kc, mb.mb.kc.n, 0.05), 4)


def calibrate_apl(
    mb,
    strengths: Sequence[float] = (0.0, 0.05, 0.10, 0.25, 0.50, 1.0, 2.0),
    train_cues: Sequence[str] = ("校准线索A", "校准线索B"),
    probe_cues: Optional[Sequence[str]] = None,
    feedback: float = 1.0,
    target_reward: float = 0.02,
) -> Dict[str, object]:
    """Sweep APL strength and pick a value that learns without saturation."""
    probe = list(probe_cues or train_cues)
    rows = []
    for strength in strengths:
        clone = copy.deepcopy(mb)
        clone.mb.apl.strength = float(strength)
        clone.mb.apl.activity = 0.0
        gain = _apl_gain_for(clone, train_cues[0])
        _train_cues(clone, train_cues, feedback)
        weights = weight_stats(clone)
        rows.append(
            {
                "strength": round(float(strength), 4),
                "gain": gain,
                "reward": round(_mean_channel(clone, probe, "reward"), 4),
                "punishment": round(_mean_channel(clone, probe, "punishment"), 4),
                "value": round(_mean_channel(clone, probe, "value"), 4),
                "max_abs": weights["max_abs"],
                "weight_total": weights["total_abs"],
            }
        )
    valid = [r for r in rows if r["max_abs"] <= 1.0 and r["weight_total"] <= 20.0]
    best = min(valid or rows, key=lambda r: abs(float(r["reward"]) - target_reward))
    return {
        "target_reward": target_reward,
        "sweep": rows,
        "best": best,
    }


def cross_seed_apl(
    seeds: Sequence[int] = tuple(range(1, 6)),
    strengths: Sequence[float] = (0.0, 0.05, 0.10, 0.25, 0.50, 1.0, 2.0),
    target_reward: float = 0.02,
) -> Dict[str, object]:
    """Run the APL sweep across seeds and check whether the best strength is stable."""
    from . import MindBase, MindConfig

    results = []
    for seed in seeds:
        seed_cues = (f"校准线索A-{seed}", f"校准线索B-{seed}")
        seed_feedback = 1.0 + 0.1 * ((int(seed) % 3) - 1)  # 0.9 / 1.0 / 1.1
        cal = calibrate_apl(
            MindBase(MindConfig(seed=seed)),
            strengths=strengths,
            train_cues=seed_cues,
            probe_cues=seed_cues,
            feedback=seed_feedback,
            target_reward=target_reward,
        )
        best = cal["best"]
        results.append(
            {
                "seed": int(seed),
                "best_strength": float(best["strength"]),
                "best_reward": float(best["reward"]),
                "best_gain": float(best["gain"]),
            }
        )
    values = [r["best_strength"] for r in results]
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(var)
    return {
        "target_reward": target_reward,
        "seeds": list(seeds),
        "results": results,
        "best_strengths": values,
        "mean_best_strength": round(mean, 4),
        "std_best_strength": round(std, 4),
        "min_best_strength": round(min(values), 4),
        "max_best_strength": round(max(values), 4),
        "stable": bool((max(values) - min(values)) <= 0.25 + 1e-9),
    }


def adapter_wording_invariance(
    seed: int = 500,
    event=None,
    feedback: float = 1.0,
    replies: Sequence[str] = ("好呀", "嗯。"),
) -> Dict[str, object]:
    """Same event + same feedback, different LLM wording: state should match."""
    from . import Event, MindBase, MindConfig
    from .llm_adapter import LLMAdapter, ScriptedLLM

    ev = event or Event(text="在吗", speaker="A", directed=True, ts=0)
    snapshots = []
    memories = []
    for reply in replies:
        mb = MindBase(MindConfig(seed=seed))
        adapter = LLMAdapter(mb, ScriptedLLM([reply]))
        adapter.handle(ev, feedback=feedback)
        snap = mb.snapshot()
        snap.pop("memory", None)  # reply text is allowed to differ
        snapshots.append(snap)
        memories.append(tuple(item.text for item in mb.memory.items))
    encoded = [json.dumps(s, sort_keys=True, ensure_ascii=False) for s in snapshots]
    return {
        "same": len(set(encoded)) == 1,
        "replies": list(replies),
        "memory_differs": len(set(memories)) > 1,
        "snapshots": snapshots,
    }


def evaluate(
    mb,
    outcomes: Optional[Iterable] = None,
    cues: Optional[Sequence[str]] = None,
    confidences: Optional[Sequence[float]] = None,
    correct: Optional[Sequence[bool]] = None,
    trained_cues: Optional[Sequence[str]] = None,
    novel_cues: Optional[Sequence[str]] = None,
    known_speakers: Optional[Sequence[str]] = None,
    novel_speakers: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    actions = [o.action for o in (outcomes if outcomes is not None else mb.history)]
    cue_list = list(cues or [])
    return {
        "health": mb.health(),
        "kc_sparsity": round(kc_sparsity(mb), 4),
        "weight": weight_stats(mb),
        "behavior_entropy": behavior_entropy(actions),
        "ece": expected_calibration_error(confidences or [], correct or []),
        "kc_separation": kc_separation(mb, cue_list),
        "channel_correlation": channel_correlation(mb, cue_list),
        "ood_gap": ood_gap(mb, list(trained_cues or []), list(novel_cues or [])),
        "ood": ood_suite(mb, list(trained_cues or []), list(novel_cues or [])),
        "cka_self": linear_cka(mb, mb, cue_list),
        "speaker_ood": speaker_ood(mb, list(known_speakers or []), list(novel_speakers or [])),
        "memory_items": len(mb.memory.items),
        "wm_slots": len(mb.wm.slots),
    }


def main() -> None:
    from . import Event, MindBase, MindConfig

    mb = MindBase(MindConfig(seed=90))
    mb.run(
        [
            Event(text="你好", speaker="A", ts=0),
            Event(text="你真棒", speaker="A", ts=60),
            Event(text="你真废物", speaker="B", ts=120),
            Event(kind="tick", ts=180),
        ]
    )
    for cue, fb in (("训练线索A", 1.0), ("训练线索B", 1.0), ("惩罚线索", -1.0)):
        mb.post_reply(cue, feedback=fb)
    report = evaluate(
        mb,
        outcomes=mb.history,
        cues=["训练线索A", "训练线索B", "惩罚线索", "全新线索"],
        confidences=[0.8, 0.6, 0.9, 0.4],
        correct=[True, True, False, False],
        trained_cues=["训练线索A", "训练线索B"],
        novel_cues=["全新线索"],
        known_speakers=["A", "B"],
        novel_speakers=["C"],
    )
    report["ablation"] = ablation_report(mb, "训练线索A", 1.0)
    report["learning_ablation"] = learning_ablation_report(mb, ["训练线索A"], ["训练线索A"], 1.0)
    report["apl_calibration"] = calibrate_apl(MindBase(MindConfig(seed=90)))
    report["apl_cross_seed"] = cross_seed_apl(seeds=range(1, 6))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
