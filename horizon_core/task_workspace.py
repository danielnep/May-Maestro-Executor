"""Repository-specific durable workspaces; never fall back to the controller."""
import hashlib
import os
from pathlib import Path
import re
import subprocess


def prepare(task, controller_root):
    payload=task.get('payload') or {}
    repo=payload.get('repository')
    if not repo:
        return Path(controller_root).resolve(), payload.get('branch')
    if not isinstance(repo,str) or not re.fullmatch(r'danielnep/[A-Za-z0-9_.-]{2,90}',repo):
        raise ValueError('workspace_repository_invalid')
    base=Path(os.getenv('HORIZON_PROJECTS_DIR','/var/lib/horizon-worker/projects')).resolve()
    group=hashlib.sha256(task['project'].encode()).hexdigest()[:16]
    root=base/repo.split('/')[1]/group
    branch=payload.get('branch') or 'agent/task-'+group
    if not re.fullmatch(r'(?:fix|feat|agent|test)/[A-Za-z0-9_./-]+',branch) or '..' in branch:
        raise ValueError('workspace_work_branch_required')
    env={k:os.environ[k] for k in ('PATH','SSL_CERT_FILE','SSL_CERT_DIR') if k in os.environ}
    env.update(HOME='/nonexistent',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',GIT_TERMINAL_PROMPT='0',GIT_ASKPASS='/bin/false')
    def git(*args):
        result=subprocess.run(['git','-c','credential.helper=','-c','core.hooksPath=/dev/null',*args],env=env,capture_output=True,text=True,timeout=180)
        if result.returncode:
            raise ValueError('workspace_git_operation_failed:'+str(args[0]))
        return result.stdout.strip()
    base.mkdir(parents=True,exist_ok=True)
    if root.exists() and (root.is_symlink() or not root.resolve().is_relative_to(base)):
        raise ValueError('workspace_path_invalid')
    if not (root/'.git').exists():
        root.parent.mkdir(parents=True,exist_ok=True)
        git('clone','--no-tags','--no-recurse-submodules','https://github.com/'+repo+'.git',str(root))
        git('-C',str(root),'checkout','-b',branch)
    if git('-C',str(root),'remote','get-url','origin') != 'https://github.com/'+repo+'.git':
        raise ValueError('workspace_origin_mismatch')
    if git('-C',str(root),'branch','--show-current') != branch:
        raise ValueError('workspace_branch_mismatch')
    return root,branch

