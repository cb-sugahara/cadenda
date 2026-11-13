import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
import pytest

from cadenda.coordination import access
from cadenda.errors import CadendaError
from cadenda.recorder import Recorder
from cadenda import backup,operations
from cadenda.config import Registry
from cadenda.security import canonical,digest


def test_maintenance_fence_blocks_writers_but_preserves_readers(registry):
    r=Recorder(registry.settings.state_root);key,owner=r.claim('test','bd-1')
    with access(r.root,exclusive=True):
        assert r.get(key)['state']=='Claimed'
        with pytest.raises(CadendaError) as exc:
            with access(r.root):pass
        assert exc.value.code=='MAINTENANCE_BUSY'
        done=[]
        t=threading.Thread(target=lambda:(r.cancel(key),done.append(True)));t.start();time.sleep(.05)
        assert not done
    t.join(2);assert done and r.get(key)['cancel_requested']==1


def test_backup_quiescence_rejects_active_or_pending_work(registry):
    from cadenda.human_store import initialize
    r=Recorder(registry.settings.state_root);initialize(r)
    key,owner=r.claim('test','bd-1')
    with pytest.raises(CadendaError):backup.quiescent(r)
    r.transition(key,owner,'Running');r.transition(key,owner,'Verifying');r.transition(key,owner,'Review')
    with r.transaction() as db:db.execute('INSERT INTO gui_jobs VALUES(?,?,?,?,NULL,NULL,?,?,NULL,?)',('job_x','test','bd-1',None,'launching',time.time(),str(r.root/'out')))
    with pytest.raises(CadendaError):backup.quiescent(r)


def test_snapshot_verifier_rejects_tamper_symlink_and_path_escape(tmp_path):
    root=tmp_path/'backup';root.mkdir();(root/'data').write_bytes(b'original')
    manifest={'schema_version':1,'files':{'data':{'sha256':digest(b'original'),'bytes':8}}}
    (root/'manifest.json').write_bytes(canonical(manifest));backup.verify(root)
    (root/'data').write_bytes(b'modified')
    with pytest.raises(CadendaError):backup.verify(root)
    (root/'data').unlink();(root/'data').symlink_to(tmp_path/'outside')
    with pytest.raises(CadendaError):backup.verify(root)
    (root/'data').unlink();manifest['files']={'../outside':{'sha256':'a'*64,'bytes':1}};(root/'manifest.json').write_bytes(canonical(manifest))
    with pytest.raises(CadendaError):backup.verify(root)


def test_health_reports_sanitized_failures_and_no_auth_file_reads(registry,monkeypatch):
    r=Recorder(registry.settings.state_root);key,owner=r.claim('test','bd-1');r.transition(key,owner,'Running');r.artifact(key,'agent-stderr',b'Authentication failed; api_key=should-not-leak');r.transition(key,owner,'NeedsHuman')
    monkeypatch.setattr(operations,'capture',lambda *a,**kw:(0,'{"mode":"embedded"}',''))
    value=operations.health(registry)
    assert any(a['code']=='AUTH_REQUIRED' for a in value['alerts'])
    assert 'should-not-leak' not in json.dumps(value)
    assert value['providers']['inference_performed'] is False


def test_supervisor_limits_restarts_and_persists_budget(registry,monkeypatch):
    registry.config_path=registry.settings.state_root.parent/'projects.yaml';launches=[];stopflag=False
    monkeypatch.setattr(operations,'health',lambda *a,**kw:{})
    def launcher(*args,**kwargs):
        launches.append(args);return SimpleNamespace(pid=99999999,poll=lambda:2,returncode=2)
    def stop():
        nonlocal stopflag
        p=registry.settings.state_root/'ops/restarts.json'
        if p.exists() and json.loads(p.read_text()).get('blocked'):stopflag=True
        return stopflag
    operations.supervise(registry,stop=stop,launcher=launcher,poll_seconds=.001)
    assert len(launches)==3
    ledger=json.loads((registry.settings.state_root/'ops/restarts.json').read_text());assert ledger['blocked']
    # Old timestamps do not erase a blocked budget after supervisor restart.
    ledger['attempts']=[0,1,2];operations.atomic(registry.settings.state_root/'ops/restarts.json',ledger)
    calls=0
    def stop_again():
        nonlocal calls
        calls+=1;return calls>4
    operations.supervise(registry,stop=stop_again,launcher=launcher,poll_seconds=.001)
    assert len(launches)==3
    operations.reset(registry);assert not json.loads((registry.settings.state_root/'ops/restarts.json').read_text()).get('blocked',False)


def test_launch_plan_has_no_shell_no_public_bind_or_infinite_launchd_loop(registry):
    import plistlib
    registry.config_path=registry.settings.state_root.parent/'projects.yaml'
    result=operations.launch_plan(registry);value=plistlib.loads(Path(result['path']).read_bytes())
    assert value['KeepAlive'] is False and value['RunAtLoad'] is True
    assert value['ProgramArguments'][1:4]==['-I','-m','cadenda.cli']
    assert not any(x in value['ProgramArguments'] for x in ['sh','bash','--host','0.0.0.0'])


def test_beads_namespace_is_pinned_and_missing_child_does_not_fall_back(registry,monkeypatch):
    import cadenda.process as process
    p=registry.project('test');calls=[]
    monkeypatch.setenv('BEADS_DIR','/unrelated/parent')
    monkeypatch.setattr(process,'capture',lambda argv,cwd,**kwargs:(calls.append(kwargs) or (0,json.dumps({'mode':'embedded','data_dir':str(p.root/'.beads/embeddeddolt/test')}),'')))
    with pytest.raises(CadendaError):process.beads_capture(['bd','list'],p.root)
    (p.root/'.beads').mkdir()
    (p.root/'.beads/metadata.json').write_text('{"dolt_mode":"embedded"}')
    process.beads_capture(['bd','list'],p.root)
    assert calls[0]['env']['BEADS_DIR']==str(p.root/'.beads')
    assert 'BEADS_DOLT_PASSWORD' not in calls[0]['env']
    (p.root/'.beads/metadata.json').write_text('{"dolt_mode":"server"}')
    with pytest.raises(CadendaError):process.beads_capture(['bd','update'],p.root)


def test_native_destination_rejects_redirected_engine_paths_before_sync(tmp_path):
    native=tmp_path/'native';native.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (native/'engine').symlink_to(outside,target_is_directory=True)
    with pytest.raises(CadendaError):backup.validate_native(native)
    assert not list(outside.iterdir())


def test_context_snapshot_publication_waits_for_backup_fence(registry):
    from cadenda.context_builder import ContextBuilder
    from conftest import FakeBeads,git_runner
    b=ContextBuilder(registry,adapter_factory=lambda _:FakeBeads(),runner=git_runner)
    initial=b.build('test','bd-1');done=[]
    with access(registry.settings.state_root,exclusive=True):
        thread=threading.Thread(target=lambda:done.append(b.build('test','bd-1')))
        thread.start();time.sleep(.08)
        assert not done
        assert len(list((registry.settings.state_root/'contexts').iterdir()))==1
    thread.join(3);assert len(done)==1


def test_restart_reset_waits_for_admission_and_never_loses_generation(registry):
    state=registry.settings.state_root;Recorder(state);operations.reset(registry)
    with operations.restart_lock(state):
        done=[];thread=threading.Thread(target=lambda:done.append(operations.reset(registry)))
        thread.start();time.sleep(.05);assert not done
        operations.atomic(state/'ops/restarts.json',{'generation':'old','attempts':[time.time()],'blocked':True})
    thread.join(2);assert done
    generation,admitted=operations.admit_start(state,1,600,time.time())
    assert generation!='old' and admitted
    assert json.loads((state/'ops/restarts.json').read_text())['blocked']
    # Even an interruption immediately after the last admission cannot age it out.
    assert operations.admit_start(state,1,600,time.time()+1000)==(generation,False)
