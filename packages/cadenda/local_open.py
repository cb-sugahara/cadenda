"""OS-owner initiated browser opening; no reusable credential in process argv."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import time
import urllib.request
from urllib.parse import urlencode

from .errors import CadendaError
from .operations import atomic
from .security import digest,read_bounded


def consume(root, code, instance):
    path = root / 'gui-open-ticket.json'
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            return False
        ticket = json.loads(read_bounded(root, path.name, 1024))
        if ticket.get('instance') != instance:
            return False
        if not 0 < ticket['expires'] - time.time() <= 60:
            return False
        if not secrets.compare_digest(ticket['sha256'], hashlib.sha256(code.encode()).hexdigest()):
            return False
    except (OSError, ValueError, KeyError, TypeError, CadendaError):
        return False
    path.unlink()
    (root / 'gui-open.html').unlink(missing_ok=True)
    return True


def open_workspace(registry, *, launch=True):
    root = registry.settings.state_root
    fd=os.open(root/'gui-server.lock',os.O_RDWR|os.O_NOFOLLOW)
    with os.fdopen(fd,'r') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:pass
        else:raise CadendaError('GUI_NOT_RUNNING','Start the GUI before opening it')
        meta=json.loads(lock.read(4096))
    instance=meta.get('instance')
    if not isinstance(instance,str) or len(instance)!=32:
        raise CadendaError('GUI_RESTART_REQUIRED','Restart the GUI before opening it')
    port = meta.get('port')
    if meta.get('local_open') != 1 or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise CadendaError('GUI_RESTART_REQUIRED', 'Start the current GUI before opening it')
    origin = f'http://127.0.0.1:{port}'
    with urllib.request.urlopen(origin + '/healthz', timeout=2) as response:
        health = json.loads(response.read(4096))
    if health != {'protocol': 1, 'workspace': digest(str(root).encode()),'instance':instance}:
        raise CadendaError('GUI_SCOPE', 'The local server belongs to another workspace')
    code = secrets.token_urlsafe(32)
    atomic(root / 'gui-open-ticket.json', {'sha256': hashlib.sha256(code.encode()).hexdigest(), 'expires': time.time() + 60,'instance':instance})
    # Launch only a private file path. The one-use code travels in a fragment,
    # is removed by the frontend before requests, and expires after one minute.
    url = origin + '/#' + urlencode({'connect': code})
    path = root / 'gui-open.html'
    temp = root / ('.gui-open-' + secrets.token_hex(8))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('<!doctype html><meta name="referrer" content="no-referrer"><meta http-equiv="refresh" content="0;url=' + url + '">')
    os.replace(temp, path)
    if launch:
        subprocess.run(['/usr/bin/open', str(path)], check=True, timeout=10, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'url': origin, 'browser_opened': launch, 'launcher_file': str(path)}
