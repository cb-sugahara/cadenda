"""Child waits until the parent has durably recorded its process group."""
import json
import os
import sys

if __name__ == '__main__':
    argv=json.loads(sys.argv[1])
    if sys.stdin.buffer.read(1)!=b'G':
        raise SystemExit(125)
    os.execvpe(argv[0],argv,os.environ)
