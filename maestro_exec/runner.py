"""Seven independently runnable Horizon agents, isolated by role and safety policy."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

OWNER = "danielnep"
PLUGIN_IDS = {
    "may-maestro": "plugins_6ad2d8cb1ff481918155636b10ce1c78",
    "corregedoria-may": "plugins_6ad2c7ad78f081918faf5ebde103166a",
    "may-persona": "plugins_6ad2b7424c9c8191aa71b4d54485b17c",
    "horizon-skills": "plugins_6ad2b3bdc9d08191abea836e62329919",
    "horizon-evolution-agent": "plugins_6ad292dd3a108191bd868a5d14609a0a",
    "horizon-project-studio": "plugins_6ad28e9f576c81919c2bcf1297e38986",
    "horizon-ui-guardian": "plugins_6ad28bfb661081919a20fd45806f3c54",
}
PROFILES: dict[str, dict[str, Any]] = {
    "may-maestro": {
        "title": "Orquestrador", "scope": "Roteamento, dependencias e consolidacao da entrega",
        "checks": ("project", "workflow", "branch", "plans"), "can_implement": False,
    },
    "corregedoria-may": {
        "title": "Corregedoria", "scope": "Fiscalizar evidencia, claims e gates",
        "checks": ("project", "secrets", "checkpoint", "branch"), "can_implement": False,
    },
    "may-persona": {
        "title": "Persona", "scope": "Copy, consistencia editorial e linguagens de UX",
        "checks": ("project", "copy", "a11y"), "can_implement": True,
    },
    "horizon-skills": {
        "title": "Engenharia", "scope": "Arquitetura, teste e integridade de contratos",
        "checks": ("project", "architecture", "test", "secrets"), "can_implement": True,
    },
    "horizon-evolution-agent": {
        "title": "Evolucao continua", "scope": "Regressao e melhoria mensuravel",
        "checks": ("project", "test", "regression", "checkpoint"), "can_implement": True,
    },
    "horizon-project-studio": {
        "title": "Estudio de projeto", "scope": "Mapear fluxos de ponta a ponta e criterios de aceite",
        "checks": ("project", "architecture", "plans", "a11y"), "can_implement": True,
    },
    "horizon-ui-guardian": {
        "title": "Guardiao UI", "scope": "Responsividade, hierarquia, acessibilidade e evidencia visual",
        "checks": ("project", "css", "a11y", "visual"), "can_implement": True,
    },
}
PROTECTED = ("main", "master", "production", "prod", "release")
BANNED_PATHS = (".env", ".git/", ".github/workflows/", "secrets/", "credentials/", "node_modules/")

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def branch_safe(branch: str) -> bool:
    return bool(re.fullmatch(r"(feat|fix|test|chore|agent)/[A-Za-z0-9._/-]{3,120}", branch)) and not any(
        part in ("..", ".", "") for part in branch.split("/")
    )

def hash_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def path_safe(root: Path, path: str) -> bool:
    if path.startswith("/") or "\\" in path or ".." in path.split("/"):
        return False
    parts=path.split("/")
    if not all(parts):
        return False
    if any(path==part or path.startswith(part) for part in BANNED_PATHS):
        return False
    try:
        return (root / path).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False

def command_run(argv: list[str], cwd: Path, timeout: int = 90) -> dict[str, Any]:
    start = time.monotonic()
    try:
        # Never let a tool's diagnostics leak commonly available credentials.
        env = {k:v for k,v in os.environ.items() if not (
            k.upper().endswith(("TOKEN","SECRET","API_KEY","PASSWORD")) or k in ("OPENAI_API_KEY","GH_TOKEN")
        )}
        completed=subprocess.run(argv,cwd=cwd,env=env,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,text=True,timeout=timeout,check=False)
        return {"status":"passed" if completed.returncode==0 else "failed",
                "exit_code":completed.returncode, "command":argv,
                "stdout_tail":completed.stdout[-4500:],"stderr_tail":completed.stderr[-1200:],
                "duration_s":round(time.monotonic()-start,2)}
    except subprocess.TimeoutExpired:
        return {"status":"failed","exit_code":124,"command":argv,"detail":"timeout","duration_s":round(time.monotonic()-start,2)}
    except FileNotFoundError:
        return {"status":"blocked","command":argv,"detail":"executable unavailable"}

class Agent:
    def __init__(self,slug: str,root: Path,evidence: Path, branch: str = "", target: str = "") -> None:
        if slug not in PROFILES:
            raise ValueError("unknown executor: "+slug)
        self.slug=slug
        self.profile=PROFILES[slug]
        self.root=root.resolve()
        self.evidence=evidence.resolve()
        self.branch=branch
        self.target=target
        self.checks: list[dict[str,Any]]=[]
        self.artifacts: list[str]=[]

    def record(self,check: str,status: str,detail: str,**extra: Any)->None:
        if status not in ("passed","failed","warning","blocked","info"):
            raise ValueError("invalid status")
        self.checks.append({"check":check,"status":status,"detail":detail,**extra})

    def project(self)->None:
        paths=[p.name for p in self.root.iterdir() if p.name not in (".git","node_modules")][:25]
        exists=any((self.root/p).exists() for p in ("README.md","package.json","pyproject.toml","server.mjs","supervisor.py"))
        self.record("project","passed" if exists else "warning","Project entrypoints inspected",paths=paths)

    def branch_check(self)->None:
        if not self.branch:
            self.record("branch","info","Read-only audit. No writable branch specified")
            return
        if not branch_safe(self.branch):
            self.record("branch","failed","Writes cannot target a non-feature branch")
            return
        git=shutil.which("git")
        if git and (self.root/".git").exists():
            observed=command_run([git,"branch","--show-current"],self.root,15)
            name=observed.get("stdout_tail","").strip()
            self.record("branch","passed" if name==self.branch else "failed",
                        "Current worktree branch check",expected=self.branch,observed=name)
        else:
            self.record("branch","warning","Git unavailable; runtime branch not independently verified",expected=self.branch)

    def workflow(self)->None:
        paths=list(self.root.glob(".github/workflows/*.yml"))+list(self.root.glob(".github/workflows/*.yaml"))
        self.record("workflow","passed" if paths else "warning","CI workflow discovery",files=[str(p.relative_to(self.root)) for p in paths[:12]])

    def architecture(self)->None:
        signatures={k:[str(p.relative_to(self.root)) for p in self.root.glob(pattern)][:10] for k,pattern in {
            "frontend":"**/*.css","scripts":"**/*.js","services":"**/*.mjs",
            "python":"**/*.py","tests":"**/test*.py","docs":"**/README.md"
        }.items()}
        self.record("architecture","passed","Repository structure inventoried",files=signatures)

    def plans(self)->None:
        plan={
            "may-maestro":["horizon-project-studio","horizon-skills","horizon-ui-guardian","horizon-evolution-agent","corregedoria-may"],
            "horizon-project-studio":["horizon-skills","horizon-ui-guardian"],
        }.get(self.slug,[])
        self.record("plans","passed","Role-specific executable dependency plan",next_agents=plan)

    def copy(self)->None:
        html=list(self.root.glob("**/*.html"))[:20]
        samples=[]
        for path in html:
            if "node_modules" in path.parts: continue
            contents=path.read_text(encoding="utf-8",errors="replace")
            if not re.search(r'lang\s*=\s*["\']pt-BR["\']',contents,re.I):
                samples.append(str(path.relative_to(self.root))+": missing pt-BR language")
        self.record("copy","warning" if samples else "passed","Static copy and locale review",issues=samples[:20],scanned=len(html))

    def css(self)->None:
        styles=[p for p in self.root.glob("**/*.css") if "node_modules" not in p.parts]
        result=[]
        for p in styles[:25]:
            c=p.read_text(encoding="utf-8",errors="replace")
            result.append({"path":str(p.relative_to(self.root)),"bytes":len(c),
                           "mobile_breakpoints":len(re.findall(r"@media",c)),
                           "focus_visible":":focus-visible" in c,
                           "reduced_motion":"prefers-reduced-motion" in c})
        self.record("css","passed" if result else "warning","Mobile CSS inspected",styles=result)

    def a11y(self)->None:
        css=list(self.root.glob("**/*.css")); html=list(self.root.glob("**/*.html"))
        css_text="\n".join(p.read_text(encoding="utf-8",errors="ignore")[:70000] for p in css[:15])
        html_text="\n".join(p.read_text(encoding="utf-8",errors="ignore")[:70000] for p in html[:15])
        summary={"focus_visible":":focus-visible" in css_text,
                 "reduced_motion":"prefers-reduced-motion" in css_text,
                 "viewport":"name=\"viewport\"" in html_text or "name='viewport'" in html_text,
                 "semantic_main":"<main" in html_text or "document.createElement('main')" in html_text}
        # Dynamic SPA HTML often contains main in JS, so static results are advisory.
        self.record("a11y","warning" if not all(summary.values()) else "passed",
                    "Static accessibility indicators (not a WCAG certification)",indicators=summary)

    def secrets(self)->None:
        # Names only, never values.
        exposed=[]
        for p in self.root.rglob("*"):
            if not p.is_file() or ".git" in p.parts or "node_modules" in p.parts: continue
            rel=str(p.relative_to(self.root))
            if re.search(r"(^|/)(\.env$|\.env\.[^/]+$|id_rsa$|.+\.pem$|credentials\.json$)",rel):
                if rel!=".env.example": exposed.append(rel)
        self.record("secrets","failed" if exposed else "passed","Potentially sensitive tracked filenames",files=exposed[:20])

    def checkpoint(self)->None:
        p=self.evidence/"checkpoint.json"
        if not p.exists():
            self.record("checkpoint","info","First run: no previous checkpoint")
            return
        try:
            data=json.loads(p.read_text(encoding="utf-8"))
            self.record("checkpoint","passed" if data.get("agent")==self.slug else "warning",
                        "Prior checkpoint consulted",last_status=data.get("status"),updated_at=data.get("created_at"))
        except (ValueError,OSError) as err:
            self.record("checkpoint","failed","Unreadable checkpoint",error=type(err).__name__)

    def regression(self)->None:
        if shutil.which("git") and (self.root/".git").exists():
            r=command_run(["git","status","--short"],self.root,15)
            self.record("regression",r["status"],"Worktree change status",summary=r.get("stdout_tail","")[-1000:])
        else:
            self.record("regression","info","Diff not available without Git; functional suite remains independent")

    def run_tests(self)->None:
        npm=(self.root/"package.json")
        if npm.exists():
            try:
                package=json.loads(npm.read_text(encoding="utf-8"))
            except ValueError:
                self.record("test","failed","Malformed package.json")
                return
            scripts=package.get("scripts",{})
            if "test" not in scripts:
                self.record("test","warning","No npm test script")
                return
            if not shutil.which("npm"):
                self.record("test","blocked","npm unavailable")
                return
            result=command_run(["npm","test"],self.root,110)
        elif (self.root/"tests").is_dir():
            result=command_run([sys.executable,"-m","unittest","discover","-s","tests","-v"],self.root,90)
        else:
            self.record("test","warning","No runnable test suite detected")
            return
        self.record("test",result["status"],"Project suite executed",
                    exit_code=result.get("exit_code"),duration_s=result.get("duration_s"),
                    stdout_tail=result.get("stdout_tail","")[-2600:],stderr_tail=result.get("stderr_tail","")[-600:])

    def visual(self,url: str = "")->None:
        if not url:
            self.record("visual","info","Browser capture not requested; use --url http://localhost:<port>/")
            return
        if not re.fullmatch(r"http://(?:localhost|127\.0\.0\.1)(?::\d{2,5})?(?:/[A-Za-z0-9._/?=&%-]*)?",url):
            self.record("visual","blocked","Visual runner accepts local isolated HTTP URLs only")
            return
        binary=next((x for x in ("google-chrome","chromium","chromium-browser","chrome") if shutil.which(x)),None)
        if not binary:
            self.record("visual","blocked","No local headless Chromium binary")
            return
        captured=[]
        for width,height in ((320,720),(390,844),(430,932)):
            filename=self.evidence/f"mobile-{width}.png"
            result=command_run([binary,"--headless","--no-sandbox","--disable-gpu",
                                "--disable-dev-shm-usage",f"--window-size={width},{height}",
                                f"--screenshot={filename}",url],self.root,35)
            ok=result["status"]=="passed" and filename.is_file() and filename.stat().st_size>1000
            if ok: self.artifacts.append(str(filename))
            captured.append({"viewport":f"{width}x{height}","status":"passed" if ok else "failed"})
        self.record("visual","passed" if all(c["status"]=="passed" for c in captured) else "failed",
                    "Real Chromium screenshot capture (not interactive QA)",viewports=captured)

    def implement(self, task_file: Path, allow_write: bool)->None:
        if not self.profile["can_implement"]:
            raise ValueError("This role is read-only: cannot implement")
        if not allow_write or not branch_safe(self.branch):
            raise ValueError("Explicit --approve-branch-edits and feat/fix/test/chore/agent branch required")
        if not (self.root/".git").exists() or not shutil.which("git"):
            raise ValueError("Implementation requires a real checked-out Git worktree")
        observed=command_run(["git","branch","--show-current"],self.root,15)
        if observed.get("stdout_tail","").strip()!=self.branch:
            raise ValueError("Worktree is not on the authorized feature branch")
        if not shutil.which("codex"):
            self.record("implement","blocked","Codex CLI not installed/authenticated. No code edits attempted.")
            return
        if not task_file.is_file() or not task_file.resolve().is_relative_to(self.root):
            raise ValueError("Task must be a file inside the checked-out repository")
        task=task_file.read_text(encoding="utf-8")
        if len(task)>12000 or len(task)<20:
            raise ValueError("Task length out of bounds")
        playbook_path=Path(__file__).resolve().parents[1]/"playbooks"/(self.slug+".md")
        if not playbook_path.is_file():
            raise ValueError("Missing agent-specific playbook: "+self.slug)
        role_policy=playbook_path.read_text(encoding="utf-8")
        directive=(
            "You are the "+self.profile["title"]+" executor for Horizon. "
            "Read the repository and current manuals. Work only inside the checked-out "
            "feature branch; do not commit, push, merge, deploy, remove secrets or change "
            "credentials. Preserve operational functionality. Run relevant tests and "
            "report what genuinely passed. Your work is reviewed before publication.\n\n"
            +"\n\n# SPECIALIZED ROLE PLAYBOOK\n"+role_policy+"\n\n# TASK\n"+task
        )
        result=command_run(["codex","exec","--sandbox","workspace-write","--skip-git-repo-check",directive],
                           self.root,135)
        self.record("implement",result["status"],"Isolated optional code executor returned",
                    exit_code=result.get("exit_code"),duration_s=result.get("duration_s"),
                    stdout_tail=result.get("stdout_tail","")[-1500:])
        # Final state: manual branch review and tests required, never auto-push.

    def run(self, mode: str="audit", url: str="", task_file: Path | None = None,
            allow_write: bool = False)->dict[str,Any]:
        if not self.root.is_dir(): raise ValueError("Repository path does not exist")
        if not self.evidence.is_relative_to(self.root):
            # For isolated ephemeral temp locations, evidence can live alongside project.
            if not self.evidence.parent.is_relative_to(self.root.parent):
                raise ValueError("Evidence destination must be within the isolated workspace")
        self.evidence.mkdir(parents=True,exist_ok=True)
        self.checkpoint()
        self.project()
        self.branch_check()
        # Detect and enforce safe scope before running external programs.
        if mode=="implement":
            if task_file is None: raise ValueError("Implementation needs a task file")
            self.implement(task_file,allow_write)
            if self.profile["can_implement"] and any(x["status"]=="passed" for x in self.checks if x["check"]=="implement"):
                self.run_tests()
        else:
            for check in self.profile["checks"]:
                if check in ("project","branch","checkpoint"): continue
                if check=="test" and mode!="test":
                    self.record("test","info","Run --mode test for executable suite")
                else:
                    fn={"workflow":self.workflow,"architecture":self.architecture,
                        "plans":self.plans,"copy":self.copy,"a11y":self.a11y,
                        "css":self.css,"secrets":self.secrets,"regression":self.regression,
                        "test":self.run_tests,"visual":lambda:self.visual(url)}[check]
                    fn()
        failed=[x for x in self.checks if x["status"]=="failed"]
        status="failed" if failed else ("blocked" if any(x["status"]=="blocked" for x in self.checks) else "passed")
        report={
            "schema":1,"agent":self.slug,"plugin_id":PLUGIN_IDS[self.slug],
            "scope":self.profile["scope"],"mode":mode,"branch":self.branch,
            "created_at":utc_now(),"status":status,"checks":self.checks,"artifacts":self.artifacts,
            "main_changed":False,"production_changed":False,
            "limitations":["Native Android features and external app credentials are not tested by this runner."],
        }
        report["sha256"]=hash_obj({k:v for k,v in report.items() if k!="sha256"})
        outfile=self.evidence/"checkpoint.json"
        outfile.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return report

def verify_checkpoint(p: Path) -> bool:
    d=json.loads(p.read_text(encoding="utf-8"))
    expected=d.pop("sha256","")
    return (bool(expected) and hash_obj(d)==expected and d.get("agent") in PROFILES
            and isinstance(d.get("checks"),list))

def main(argv: list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description="Maestro: executores independentes, seguros e verificaveis")
    parser.add_argument("--agent",choices=list(PROFILES)+["all"],default="may-maestro")
    parser.add_argument("--mode",choices=["audit","test","implement"],default="audit")
    parser.add_argument("--root",type=Path,default=Path("."))
    parser.add_argument("--evidence",type=Path,default=Path("evidencias"))
    parser.add_argument("--branch",default="")
    parser.add_argument("--url",default="")
    parser.add_argument("--task-file",type=Path)
    parser.add_argument("--approve-branch-edits",action="store_true")
    parser.add_argument("--list",action="store_true")
    parser.add_argument("--verify",type=Path)
    args=parser.parse_args(argv)
    if args.list:
        print(json.dumps({k:{"plugin_id":PLUGIN_IDS[k],**v} for k,v in PROFILES.items()},ensure_ascii=False,indent=2))
        return 0
    if args.verify:
        ok=verify_checkpoint(args.verify)
        print("PASS: checkpoint integrity" if ok else "FAIL: checkpoint integrity")
        return 0 if ok else 1
    if args.agent=="all" and args.mode=="implement":
        parser.error("Implementation must target exactly one agent")
    slugs=list(PROFILES) if args.agent=="all" else [args.agent]
    results=[]
    for slug in slugs:
        root=args.root.resolve()
        evidence=(root/args.evidence/slug).resolve()
        try:
            result=Agent(slug,root,evidence,args.branch).run(args.mode,args.url,args.task_file,args.approve_branch_edits)
        except (ValueError,OSError) as err:
            print(f"FAIL {slug}: {type(err).__name__}: {err}",file=sys.stderr)
            return 2
        results.append({"agent":slug,"status":result["status"],"checkpoint":str(evidence/"checkpoint.json")})
    print(json.dumps(results,ensure_ascii=False,indent=2))
    return 1 if any(x["status"]=="failed" for x in results) else 0

if __name__=="__main__":
    raise SystemExit(main())

