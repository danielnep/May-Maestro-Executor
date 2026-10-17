#!/usr/bin/env python3
"""Maestro Supervisor: read-only project discovery; Gemini suggests tasks for May.
Only permitted write is opening a task issue in the Maestro repository.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

OWNER = "danielnep"
MAILBOX = "May-Maestro-Executor"
PROJECTS = ["app-hondas-maxvel", "romaneio-digital", "michelle-preview", "horizon-preview", "horizon-site"]
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

def github(method, path, payload=None):
    headers = {"User-Agent": "MayMaestro/1.0", "Accept": "application/vnd.github+json"}
    if os.getenv("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = Request("https://api.github.com" + path,
                  data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers, method=method)
    with urlopen(req, timeout=25) as resp:
        return json.load(resp)

def fingerprint(signal):
    return hashlib.sha256(signal["key"].encode()).hexdigest()[:20]

def discover(get=github, today=None):
    today = today or dt.datetime.now(dt.timezone.utc)
    week = today.strftime("%G-W%V")
    found = []
    for project in PROJECTS:
        try:
            r = get("GET", "/repos/" + OWNER + "/" + project)
            history = get("GET", "/repos/" + OWNER + "/" + project + "/actions/runs?per_page=12")
        except (HTTPError, OSError, ValueError) as err:
            print("Repository unavailable:", project, type(err).__name__, file=sys.stderr)
            continue
        seen = set()
        failures = 0
        for run in history.get("workflow_runs", []):
            name = re.sub(r"\s+", " ", str(run.get("name", "CI"))).strip()[:85]
            if name in seen:
                continue
            seen.add(name)
            if run.get("conclusion") != "failure":
                continue
            failures += 1
            found.append({"key":project + ":ci:" + name, "project":project,
                          "kind":"ci_failure", "summary":"Workflow failed: "+name,
                          "url":run.get("html_url","")})
            if failures >= 2:
                break
        if failures == 0:
            found.append({"key":project + ":review:" + week, "project":project,
                          "kind":"periodic_review",
                          "summary":"Review existing UI, regression tests and delivery opportunities; this is not a confirmed defect.",
                          "url":r.get("html_url", "https://github.com/"+OWNER+"/"+project)})
    return found

def validate(plan, signals):
    valid = {s["key"]:s for s in signals}
    output = []
    used = set()
    items = plan.get("tasks", [])
    if not isinstance(items,list):
        raise ValueError("Gemini tasks must be a list")
    for t in items[:2]:
        if not isinstance(t,dict) or t.get("signal_key") not in valid or t["signal_key"] in used:
            continue
        title, instruction, checklist = t.get("title"), t.get("instruction"), t.get("acceptance")
        if not isinstance(title,str) or not 8 <= len(title) <= 110:
            continue
        if not isinstance(instruction,str) or not 25 <= len(instruction) <= 1400:
            continue
        if not isinstance(checklist,list) or not 1 <= len(checklist) <= 4:
            continue
        if not all(isinstance(a,str) and 5 <= len(a) <= 170 for a in checklist):
            continue
        used.add(t["signal_key"])
        output.append({"signal":valid[t["signal_key"]],
                       "title":title.strip(), "instruction":instruction.strip(),
                       "acceptance":checklist})
    return output

def plan_with_gemini(signals, sender=urlopen):
    if not signals:
        return []
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY absent")
    instruction = (
        "You are the proactive May Maestro supervisor, not the coder. "
        "May inside ChatGPT is the ONLY developer. You never change source code, "
        "files, branches, pull requests, deployments or user apps. "
        "You can request up to two specific jobs for May, or zero. "
        "Choose ONLY evidence signal_key values given. Prioritize real failed CI "
        "and fastest commercial deliveries. Do not invent defects or causes. "
        "Produce Brazilian Portuguese JSON exactly: "
        "{tasks:[{signal_key,title,instruction,acceptance:[string]}]}. "
        "Every instruction addresses May and asks for evidence/tests; "
        "do not include secrets or customer personal data."
    )
    body = {"systemInstruction":{"parts":[{"text":instruction}]},
            "contents":[{"parts":[{"text":json.dumps(signals[:14],ensure_ascii=False)}]}],
            "generationConfig":{"temperature":0.1,"maxOutputTokens":1200,"responseMimeType":"application/json"}}
    req = Request("https://generativelanguage.googleapis.com/v1beta/models/"+MODEL+":generateContent",
                  data=json.dumps(body).encode(), method="POST",
                  headers={"Content-Type":"application/json","x-goog-api-key":key})
    try:
        with sender(req,timeout=65) as r:
            result=json.load(r)
    except HTTPError as e:
        raise RuntimeError("Gemini API error: HTTP "+str(e.code)) from None
    parts=result.get("candidates",[{}])[0].get("content",{}).get("parts",[])
    text="".join(p.get("text","") for p in parts)
    if not text:
        raise RuntimeError("Gemini returned no proposals")
    return validate(json.loads(text),signals)

def body_for(t):
    s=t["signal"]
    url=s["url"]
    if not (isinstance(url,str) and url.startswith("https://github.com/"+OWNER+"/")
            and not any(x in url for x in ["\n","\r","<",">"])):
        raise ValueError("Untrusted evidence URL")
    check="\n".join("- [ ] "+x for x in t["acceptance"])
    return ("## May Maestro | Solicitação para a May\n\n"
            "**Projeto:** "+OWNER+"/"+s["project"]+"\n"
            "**Sinal:** "+s["kind"]+"\n"
            "**Evidência:** "+url+"\n\n"
            "### Trabalho reservado à May no ChatGPT\n"+t["instruction"]+"\n\n"
            "### Critérios de conclusão\n"+check+"\n\n"
            "O Gemini somente analisou e priorizou. Nenhum código, branch ou deploy foi alterado.\n"
            "May deve ler os manuais antes de editar, testar e preservar a produção.\n\n"
            "<!-- maestro-signal:"+fingerprint(s)+" -->")

def publish(tasks, api=github, dry_run=False):
    current = api("GET","/repos/"+OWNER+"/"+MAILBOX+"/issues?state=all&per_page=100")
    known="\n".join((i.get("body") or "") for i in current if "pull_request" not in i)
    made=[]
    for t in tasks:
        marker="<!-- maestro-signal:"+fingerprint(t["signal"])+" -->"
        if marker in known:
            print("Already registered:",t["signal"]["key"])
            continue
        issue={"title":"[Maestro] "+t["title"],"body":body_for(t)}
        if dry_run:
            made.append(issue["title"])
        else:
            if not os.getenv("GH_TOKEN"):
                raise RuntimeError("GH_TOKEN missing")
            created=api("POST","/repos/"+OWNER+"/"+MAILBOX+"/issues",issue)
            made.append(created["html_url"])
        known+="\n"+issue["body"]
    return made

def self_test():
    def fake(method,url,payload=None):
        if url.endswith("/issues?state=all&per_page=100"):
            return []
        if "/actions/runs" in url:
            return {"workflow_runs":[{"name":"Build", "conclusion":"failure",
                      "html_url":"https://github.com/danielnep/romaneio-digital/actions/runs/1"}]}
        return {"html_url":"https://github.com/danielnep/example"}
    signals=discover(fake,dt.datetime(2026,10,17,tzinfo=dt.timezone.utc))
    assert len(signals)==len(PROJECTS)
    task={"signal_key":signals[0]["key"],"title":"Investigar build com falha",
          "instruction":"May, consulte os logs, encontre a causa e proponha uma correção testada.",
          "acceptance":["Logs conferidos antes da implementação"]}
    valid=validate({"tasks":[task,{"signal_key":"inventado","title":"Falso"}]},signals)
    assert len(valid)==1
    assert len(publish(valid,fake,True))==1
    assert "maestro-signal:" in body_for(valid[0])
    print("PASS: supervisor self-tests")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        self_test()
        return
    signals=discover()
    print("Evidence signals:",len(signals))
    if args.dry_run:
        print(json.dumps(signals,ensure_ascii=False,indent=2))
        return
    tasks=plan_with_gemini(signals)
    print("Gemini proposals:",len(tasks))
    print("New Maestro tasks:",json.dumps(publish(tasks),ensure_ascii=False))

if __name__=="__main__":
    main()
