from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .proactive_rank import auc, fit, proactive_pairs

DATA = Path(__file__).resolve().parent.parent / "data" / "qq_target_dataset.jsonl"


def run():
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    byg = defaultdict(list)
    for r in rows: byg[r["group"]].append(r)
    groups = [g for g, v in sorted(byg.items(), key=lambda kv: -len(kv[1]))]
    out = {}
    for g in groups:
        tr = proactive_pairs([r for x, v in byg.items() if x != g for r in v])
        te = proactive_pairs(byg[g])
        if len(tr) < 100 or len(te) < 20: continue
        model, mu, sd = fit(tr)
        out[g] = {"n": len(te), "auc": round(auc(te, model, mu, sd), 4)}
    vals = [v["auc"] for v in out.values()]
    return {"groups": len(out), "mean_auc": round(sum(vals) / max(1, len(vals)), 4), "results": out}


if __name__ == "__main__":
    import json as j
    print(j.dumps(run(), ensure_ascii=False, indent=2))
