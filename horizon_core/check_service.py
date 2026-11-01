"""Narrow Unix-socket broker: fixed sandboxed checks, never arbitrary shell."""
import json
import os
from pathlib import Path
import pwd
import socket
import socketserver
import struct
from .tools import Tools

SOCKET='/run/horizon-checks/check.sock'
ALLOWED={'python-unittest','node-test','browser-playwright','android-gradle','android-release','runtime-health'}


def request_check(root,args):
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(700)
        conn.connect(os.environ['HORIZON_CHECK_SOCKET'])
        conn.sendall(json.dumps({'root':str(root),'args':args}).encode()+b'\n')
        stream=conn.makefile('rb');raw=stream.readline(12_000_000)
    result=json.loads(raw)
    if 'broker_error' in result:
        raise RuntimeError(result['broker_error'])
    return result


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        uid=pwd.getpwnam('horizon-worker').pw_uid
        peer=struct.unpack('3i',self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
        try:
            if peer!=uid:
                raise ValueError('check_peer_denied')
            packet=json.loads(self.rfile.readline(8193))
            root=Path(packet['root'])
            allowed=(Path('/var/lib/horizon-worker/projects'),Path('/var/lib/horizon-worker/workspace'))
            if not root.is_absolute() or root.is_symlink() or not any(root.resolve().is_relative_to(p) for p in allowed):
                raise ValueError('check_root_denied')
            args=packet['args']
            if set(args)-{'name','project_dir'} or args.get('name') not in ALLOWED:
                raise ValueError('check_not_allowlisted')
            if args['name']=='runtime-health':
                import sqlite3,subprocess,time,uuid
                from .registry import AGENT_IDS,load_agent
                tested=[]
                for aid in AGENT_IDS:load_agent(aid)
                tested.append('independent specialist configurations loaded: '+str(len(AGENT_IDS)))
                with sqlite3.connect('file:/var/lib/horizon-worker/state/active.sqlite3?mode=ro',uri=True) as db:
                    count=db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
                tested.append('durable queue readable: '+str(count)+' tasks')
                active=subprocess.run(['systemctl','is-active','horizon-worker'],text=True,capture_output=True,timeout=5)
                tested.append('horizon-worker: '+active.stdout.strip())
                tested.append('check broker Unix-socket peer authenticated')
                result={'check':'runtime-health','source':'runtime','backend':'read-only-vps-health',
                    'simulated':False,'passed':active.returncode==0,'exit_code':active.returncode,'tests_run':4,
                    'executed_at':time.time(),'execution_id':uuid.uuid4().hex,'output':'\n'.join(tested),
                    'cleanup_ok':True,'workspace_unchanged':True,'timed_out':False,'truncated':False}
            else:result=Tools(root,{'tools':['run_check']}).run_check(args)
            # Evidence is created by the broker, readable by its owning worker.
            evidence=root/'runtime'
            if evidence.exists():
                for path in [evidence,*evidence.rglob('*')]:
                    if not path.is_symlink():os.chown(path,uid,pwd.getpwnam('horizon-worker').pw_gid)
        except Exception as error:
            result={'broker_error':type(error).__name__+': '+str(error)[:180]}
        self.wfile.write(json.dumps(result).encode()+b'\n')


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True


def main():
    os.environ.pop('HORIZON_CHECK_SOCKET',None)
    socket_path=Path(SOCKET);socket_path.parent.mkdir(parents=True,exist_ok=True)
    socket_path.unlink(missing_ok=True)
    with Server(SOCKET,Handler) as server:
        os.chown(SOCKET,0,pwd.getpwnam('horizon-worker').pw_gid);os.chmod(SOCKET,0o660)
        server.serve_forever()


if __name__=='__main__':main()
