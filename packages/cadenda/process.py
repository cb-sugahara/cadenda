"""No-shell, bounded-output process runner for read-only CLI adapters."""
import os
import fcntl
import threading
from contextlib import contextmanager
from pathlib import Path
import selectors
import signal
import subprocess
import time

from .errors import CadendaError


def capture(argv: list[str], cwd: Path, *, timeout: float = 20, max_bytes: int = 4_194_304, env=None):
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            env=(os.environ if env is None else env) | {"NO_COLOR": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    except FileNotFoundError as exc:
        raise CadendaError("CLI_MISSING", f"Executable not found: {argv[0]}") from exc
    except OSError as exc:
        raise CadendaError("PERMISSION", "Could not start local CLI") from exc
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    completed = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CadendaError("TIMEOUT", f"Local CLI exceeded {timeout}s")
                for key, _ in selector.select(min(remaining, 0.2)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffers[key.data].extend(chunk)
                    if sum(map(len, buffers.values())) > max_bytes:
                        raise CadendaError("OUTPUT_LIMIT", f"Local CLI output exceeded {max_bytes} bytes")
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise CadendaError("TIMEOUT", "Local CLI did not exit after closing its output") from exc
            completed = True
    finally:
        if not completed or process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
    return process.returncode, *(bytes(buffers[name]).decode("utf-8", errors="replace") for name in ("stdout", "stderr"))


_beads_locks=threading.local()


@contextmanager
def beads_guard(cwd):
    directory=cwd/'.beads'
    if not directory.is_dir() or directory.resolve()!=directory or (directory/'redirect').exists():raise CadendaError('PROJECT_UNINITIALIZED','Register the primary embedded repository')
    held=getattr(_beads_locks,'held',set())
    if str(directory) in held:
        yield;return
    fd=os.open(directory/'cadenda-operations.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        _beads_locks.held=held|{str(directory)}
        try:yield
        finally:_beads_locks.held=held


def beads_capture(argv,cwd,**kwargs):
    with beads_guard(cwd):return _beads_capture(argv,cwd,**kwargs)


def _beads_capture(argv,cwd,**kwargs):
    """Pin the Beads namespace; never discover an ancestor or ambient BEADS_DIR."""
    directory=cwd/'.beads'
    if not directory.is_dir() or directory.resolve()!=directory or (directory/'redirect').exists():
        raise CadendaError('PROJECT_UNINITIALIZED','Register the primary repository with its own canonical Beads directory')
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR','SYSTEMROOT'}}
    env.update({'BEADS_DIR':str(directory),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'core.hooksPath','GIT_CONFIG_VALUE_0':'/dev/null','GIT_CONFIG_KEY_1':'core.fsmonitor','GIT_CONFIG_VALUE_1':'false'})
    import json
    from .security import read_bounded
    try:
        metadata=json.loads(read_bounded(directory,'metadata.json',65536))
        if metadata.get('dolt_mode')!='embedded':raise ValueError()
        code,out,_=capture(['bd','--readonly','--sandbox','--directory',str(cwd),'dolt','status','--json'],cwd,env=env,timeout=10,max_bytes=65536)
        status=json.loads(out)
        location=Path(status.get('data_dir',''))
        if code or status.get('mode')!='embedded' or location.resolve()!=location or not location.is_relative_to(directory):raise ValueError()
    except (OSError,ValueError,CadendaError):raise CadendaError('BEADS_SCOPE','Only a primary embedded database inside the project is supported')
    return capture(argv,cwd,env=env,**kwargs)
