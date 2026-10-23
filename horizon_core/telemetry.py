"""Minimal, private VPS telemetry. No prompts, responses, tokens or credentials are written."""
import datetime
import json
import os
import re
from pathlib import Path

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9:-]{2,109}$")
_MODEL = re.compile(r"^[a-zA-Z0-9._/-]{1,90}$")
_ACTIONS = frozenset({"thinking","read_file","list_files","write_file","run_check","finish",
                      "remember","delegate","search_web","fetch_public","inspect_image","started"})
_SAFE_FILE = re.compile(r"^[a-zA-Z0-9_./-]{1,140}$")


def _telemetry_file(kind):
    configured = os.environ.get("HORIZON_DIRECT_INBOX_DIR")
    # The dedicated VPS worker sets this path explicitly. Do not write artifacts to
    # arbitrary developer machines or agent scratch worktrees.
    if not configured:
        return None
    root = Path(configured).resolve()
    directory = root / "telemetry"
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return directory / (kind + "-" + day + ".jsonl")


def _write(kind, packet):
    try:
        path = _telemetry_file(kind)
        if path is None:
            return False
        payload = (json.dumps(packet,ensure_ascii=False,separators=(",",":"))+"\n").encode("utf-8")
        if len(payload)>600:
            return False
        descriptor = os.open(str(path),os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        try:
            # Records are small, one append per provider response or tool action.
            os.write(descriptor,payload)
        finally:
            os.close(descriptor)
        return True
    except (OSError,ValueError,TypeError):
        # Monitoring must never interrupt the actual specialist's work.
        return False


def _count(value):
    return value if type(value) is int and 0<=value<=100000000 else None


def record_usage(agent,provider,model,usage,task_id=None):
    if not (isinstance(agent,str) and _ID.fullmatch(agent) and
            provider in ("gemini","groq") and isinstance(model,str) and
            _MODEL.fullmatch(model) and isinstance(usage,dict)):
        return False
    if provider=="gemini":
        sent=_count(usage.get("promptTokenCount"))
        received=_count(usage.get("candidatesTokenCount"))
        total=_count(usage.get("totalTokenCount"))
        reasoning=_count(usage.get("thoughtsTokenCount"))
    else:
        sent=_count(usage.get("prompt_tokens"))
        received=_count(usage.get("completion_tokens"))
        total=_count(usage.get("total_tokens"))
        reasoning=None
    if sent is None and received is None and total is None:
        return False
    if total is None and sent is not None and received is not None:
        total=sent+received
    safe_task = task_id if isinstance(task_id,str) and _TASK_ID.fullmatch(task_id) else None
    return _write("usage",{
        "taskId":safe_task,"at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "agent":agent,"provider":provider,"model":model,
        "input":sent,"output":received,"reasoning":reasoning,
        "total":total,"source":"provider_response"})


def record_activity(task_id,agent,action,step=None,path=None):
    if not (isinstance(task_id,str) and _TASK_ID.fullmatch(task_id)
            and isinstance(agent,str) and _ID.fullmatch(agent)
            and action in _ACTIONS):
        return False
    safe_path=None
    if isinstance(path,str) and _SAFE_FILE.fullmatch(path) and not any(
            marker in path.lower() for marker in (".env","secret","token","password","credential","private_key")):
        safe_path=path
    return _write("activity",{
        "at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "taskId":task_id,"agent":agent,"action":action,
        "step":_count(step),"path":safe_path})
