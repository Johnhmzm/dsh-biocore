from __future__ import annotations
import json, os, statistics, urllib.request
from pathlib import Path
from mindbase.training.auto_eval import ask

DATA = Path(__file__).resolve().parent.parent / "data" / "style_ab.jsonl"
SRC = Path(__file__).resolve().parent.parent / "data" / "exusiai_1v1_records.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "style_expand_ab.jsonl"

def expand(key, user, reply):
    prompt = (
        "把下面这句话扩写成 50-70 字、2-3 句；保留原来的语气和意思，"
        "不要客服腔，不要问新问题，不要解释。只输出扩写后的句子。\n\n"
        "用户：" + user + "\n原回复：" + reply
    )
    body = json.dumps({"model":"deepseek-flash","messages":[{"role":"user","content":prompt}],"max_tokens":512,"temperature":0.7}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data=json.loads(r.read().decode())
    return str(data["choices"][0]["message"]["content"] or "").strip()

def run():
    key=os.getenv("DEEPSEEK_API_KEY") or ""
    rows=[json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    src={r["id"]:r for r in (json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip())}
    out=[]
    for r in rows:
        rep=str(r["reply"]); new=rep
        user=str((src.get(r["id"]) or {}).get("user_text") or "")
        if len(rep)<45:
            for attempt in range(2):
                try: cand=expand(key,user,rep)
                except Exception: cand=""
                if len(cand)>=10:
                    new=cand; break
        j=ask(key, user, new)
        out.append({"id":r["id"],"before":rep,"after":new,"judge":j})
    with OUT.open("w",encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    dims=["relevance","natural","empathy","persona","length"]
    return {"n":len(out),"len_before":round(statistics.mean(len(r["before"]) for r in out),1),"len_after":round(statistics.mean(len(r["after"]) for r in out),1),"judge":{d:round(statistics.mean(float(r["judge"].get(d) or 3) for r in out),3) for d in dims},"ratings":{k:sum(1 for r in out if r["judge"].get("rating")==k) for k in ("good","mid","bad")}}

if __name__=="__main__": print(run())
