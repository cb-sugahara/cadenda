import json
from pathlib import Path
import subprocess
import sys
import cadenda.worker


def test_no_exec_when_parent_dies_before_persisting_pid(tmp_path):
    sentinel=tmp_path/'executed'
    argv=[sys.executable,'-c',f'from pathlib import Path;Path({str(sentinel)!r}).touch()']
    gate=Path(cadenda.worker.__file__).with_name('launch_gate.py')
    process=subprocess.Popen([sys.executable,str(gate),json.dumps(argv)],stdin=subprocess.PIPE)
    process.stdin.close();process.wait(timeout=5)
    assert process.returncode==125 and not sentinel.exists()
