from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .qq_rank import feat, pairs
from .qq_rank_gate import fit
from .qq_shadow import aggregate, auc, events, metrics

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def score_margin(evs, model, mu, sd):
    import torch
    out = []
    for ev in evs:
        X = (torch.tensor([feat(r) for r in ev], dtype=torch.float32) - mu) / sd
        with torch.no_grad():
            out.append(aggregate(model(X).detach().cpu().numpy().reshape(-1).tolist())["margin"])
    return out


def run() -> dict:
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    byg = defaultdict(list)
    for r in rows: byg[r["group"]].append(r)
    groups = [g for g, v in sorted(byg.items(), key=lambda kv: -len(kv[1])) if len(events(v)) >= 30]
    out = {}
    for i, test_group in enumerate(groups):
        val_group = groups[(i + 1) % len(groups)]
        if val_group == test_group: continue
        train_rows = [r for g, v in byg.items() if g not in (test_group, val_group) for r in v]
        val_ev = events(byg[val_group])
        test_ev = events(byg[test_group])
        if len(pairs(train_rows)) < 100 or len(val_ev) < 20 or len(test_ev) < 20:
            continue
        model, mu, sd = fit(pairs(train_rows))
        yv = [1 if any(r["label"] == "speak" for r in ev) else 0 for ev in val_ev]
        yt = [1 if any(r["label"] == "speak" for r in ev) else 0 for ev in test_ev]
        sv = score_margin(val_ev, model, mu, sd)
        st = score_margin(test_ev, model, mu, sd)
        best = (-1.0, 0.0)
        for th in sorted(set(sv)):
            m = metrics(yv, sv, th)
            if m["balanced"] > best[0]: best = (m["balanced"], th)
        th = best[1]
        mt = metrics(yt, st, th)
        out[test_group] = {
            "n": len(test_ev), "val_group": val_group,
            "val_balanced": round(best[0], 4), "threshold": round(th, 4),
            "test_balanced": mt["balanced"], "test_accuracy": mt["accuracy"],
            "test_auc": round(auc(yt, st), 4), "speak_pred": mt["speak_pred"],
        }
    vals = [v["test_balanced"] for v in out.values()]
    aucs = [v["test_auc"] for v in out.values()]
    return {
        "groups": len(out),
        "mean_balanced": round(sum(vals) / max(1, len(vals)), 4),
        "mean_auc": round(sum(aucs) / max(1, len(aucs)), 4),
        "results": out,
    }


if __name__ == "__main__":
    import json as j
    print(j.dumps(run(), ensure_ascii=False, indent=2))
