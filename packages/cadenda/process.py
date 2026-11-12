"""No-shell, bounded-output process runner for read-only CLI adapters."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from .errors import CadendaError


def capture(argv: list[str], cwd: Path, *, timeout: float = 20, max_bytes: int = 4_194_304):
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            env=os.environ | {"NO_COLOR": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
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
