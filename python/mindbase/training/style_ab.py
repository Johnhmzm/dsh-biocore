from __future__ import annotations
import json, os, re, statistics
from pathlib import Path
from mindbase.llm_openai import OpenAICompatClient
from mindbase.one_on_one import OneOnOneSession
from mindbase.training.auto_eval import ask

DATA = Path(__file__).resolve().parent.parent / "data" / "exusiai_1v1_records.jsonl"
OUT = Path(__file__).resolve().parent.parent / "data" / "style_ab.jsonl"
PROMPT = (
    "你是她的表达层。像能天使：2–3 句，40–60 字，口语，偶尔感叹或反问；"
    "先接住情绪，不要客服腔，不要每句都问。只输出对用户说的话。"
)

def run(n=30):
    key=os.getenv("DEEPSEEK_API_KEY") or ""
    rows=[json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()][:n]
    client=OpenAICompatClient(model="deepseek-flash",base_url="https://api.deepseek.com",api_key=key,temperature=.8,character_prompt=PROMPT)
    out=[]
    for i,r in enumerate(rows):
        reply=""
        for a in range(3):
            s=OneOnOneSession(seed=7000+i*3+a,client=client,log_path=OUT.with_suffix(".log.jsonl"))
            reply=str(s.step(str(r["user_text"])).get("reply") or "").strip()
            if len(reply)>=4: break
        j=ask(key,str(r["user_text"]),reply)
        out.append({"id":r["id"],"reply":reply,"actual":r["reply"],**j})
    with OUT.open("w",encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    def st(key):
        ts=[str(r.get(key) or "") for r in out]; lens=[len(t) for t in ts]
        return {"len":round(statistics.mean(lens),1),"q":round(sum(1 for t in ts if "?" in t or "？" in t)/len(ts),3),"excl":round(sum(1 for t in ts if "!" in t or "！" in t)/len(ts),3)}
    dims=["relevance","natural","empathy","persona","length"]
    return {"n":len(out),"candidate":st("reply"),"actual":st("actual"),"judge":{d:round(statistics.mean(float(r.get(d) or 3) for r in out),3) for d in dims},"ratings":{k:sum(1 for r in out if r.get("rating")==k) for k in ("good","mid","bad")}}

if __name__=="__main__": print(run())
