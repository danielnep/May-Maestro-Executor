"""Sleep until new queue data or the exact retry deadline; no idle model calls."""
import argparse
import ctypes
import os
from pathlib import Path
import select
import sqlite3
import time


def delay(db_path,now=None):
    now=time.time() if now is None else now
    with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT t.not_before FROM tasks t WHERE t.status='queued' AND NOT EXISTS (SELECT 1 FROM json_each(t.depends_on) d LEFT JOIN tasks p ON p.task_id=d.value WHERE p.status IS NULL OR p.status!='succeeded')").fetchall()
    return max(0,min(300,min((r[0]-now for r in rows),default=300)))


def wait(db_path,queue):
    libc=ctypes.CDLL(None,use_errno=True);fd=libc.inotify_init1(os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        if fd>=0:
            for path in (Path(db_path).parent,Path(queue)):
                libc.inotify_add_watch(fd,os.fsencode(path),0x2|0x8|0x80|0x100)
        seconds=delay(db_path)
        if seconds>0:
            if fd>=0:select.select([fd],[],[],seconds)
            else:time.sleep(min(seconds,30))
    finally:
        if fd>=0:os.close(fd)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--queue',required=True)
    args=p.parse_args();wait(args.db,args.queue)
