"""Advisory maintenance fence shared by all Cadenda writers."""
from contextlib import contextmanager
import fcntl
import os
import time
from .errors import CadendaError


@contextmanager
def access(root, *, exclusive=False, wait=0):
    if root.resolve()!=root:raise CadendaError('PATH_DENIED','State root must be canonical')
    fd=os.open(root/'maintenance.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as guard:
        deadline=time.monotonic()+wait
        while True:
            try:
                fcntl.flock(guard,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise CadendaError('MAINTENANCE_BUSY','Backup maintenance is in progress; retry explicitly later')
                time.sleep(.02)
        yield
