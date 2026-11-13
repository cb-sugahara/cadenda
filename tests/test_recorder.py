import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from cadenda.errors import CadendaError
from cadenda.recorder import Recorder
from cadenda.worker import execute


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path.resolve()/'state')


def test_atomic_global_claim_and_illegal_transition(recorder):
    key,owner=recorder.claim('p','i')
    with pytest.raises(CadendaError): Recorder(recorder.root).claim('other','issue')
    with pytest.raises(CadendaError): recorder.transition(key,owner,'Review')
    recorder.transition(key,owner,'Running');recorder.transition(key,owner,'Verifying');recorder.transition(key,owner,'Review')
    with pytest.raises(CadendaError):recorder.claim('p','i')
    recorder.claim('p','different')


def test_two_concurrent_connections_one_claim(recorder):
    results=[]
    def claim():
        try: results.append(recorder.claim('p','i'))
        except CadendaError: results.append(None)
    threads=[threading.Thread(target=claim) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert sum(r is not None for r in results)==1


def test_timeout_output_and_cancel_preserve_logs(recorder,tmp_path):
    key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running')
    with pytest.raises(CadendaError) as exc:
        execute(recorder,key,owner,'timeout',[sys.executable,'-c','import time;print("hello",flush=True);time.sleep(10)'],tmp_path,.2)
    assert exc.value.code=='TIMEOUT'
    assert 'hello' in (recorder.root/'artifacts'/key/'timeout-stdout.txt').read_text()
    recorder.cancel(key)
    with pytest.raises(CadendaError) as exc:execute(recorder,key,owner,'canceled',[sys.executable,'-c','print(1)'],tmp_path)
    assert exc.value.code=='CANCELED'


def test_async_cancel_kills_process_group(recorder,tmp_path):
    key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running')
    timer=threading.Timer(.3,lambda:recorder.cancel(key));timer.start()
    try:
        with pytest.raises(CadendaError) as exc:
            execute(recorder,key,owner,'cancel',[sys.executable,'-c','import time;time.sleep(10)'],tmp_path)
        assert exc.value.code=='CANCELED'
    finally:timer.join()
    assert not recorder.alive(recorder.get(key)['pgid'])


def test_retry_limit_and_failure_cannot_be_reviewed(recorder):
    for n in range(3):
        key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running');recorder.transition(key,owner,'RetryableFailure')
        with pytest.raises(CadendaError):recorder.transition(key,owner,'Review')
    with pytest.raises(CadendaError) as exc:recorder.claim('p','i')
    assert exc.value.code=='RETRY_LIMIT'


def test_explicit_diagnostic_recheck_does_not_restart_implementation(recorder):
    key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running');recorder.transition(key,owner,'Verifying');recorder.transition(key,owner,'Review')
    with pytest.raises(CadendaError):recorder.claim('p','i')
    new,_=recorder.claim('p','i',verification=True)
    assert new!=key and recorder.get(new)['attempt']==1


def test_recovery_does_not_use_expired_lease_and_blocks_until_release(recorder):
    key,owner=recorder.claim('p','i')
    with recorder.transaction() as db:db.execute('UPDATE runs SET lease_expires_at=0 WHERE run_id=?',(key,))
    assert recorder.recover()==[] # live worker cannot be stolen
    with recorder.transaction() as db:db.execute('UPDATE runs SET worker_pid=NULL WHERE run_id=?',(key,))
    assert recorder.recover()==[key]
    with pytest.raises(CadendaError):recorder.claim('p','i')
    with pytest.raises(CadendaError):recorder.transition(key,owner,'Running')
    recorder.release(key)
    recorder.claim('p','i')


def test_artifact_redaction_and_catalog(recorder):
    key,owner=recorder.claim('p','i')
    recorder.artifact(key,'result',b'api_key=sk-abcdefghijklmnopqrstuvwxyz123456','test')
    path=recorder.root/'artifacts'/key/'result.txt'
    assert 'sk-' not in path.read_text()
    with pytest.raises(FileExistsError):recorder.artifact(key,'result',b'replacement')
    recorder.transition(key,owner,'Running');recorder.transition(key,owner,'Verifying');recorder.transition(key,owner,'Review')
    rows=Recorder(recorder.root,initialize=False).catalog_runs('p')
    assert rows[0].artifacts[0].sha256 and rows[0].outcome=='succeeded'


def test_outbox_reconciles_crash_after_external_send(recorder,registry):
    key,owner=recorder.claim('test','bd-1');comments=[];fail=True
    def runner(argv,cwd):
        nonlocal fail
        if 'add' in argv:
            comments.append({'issue_id':'bd-1','text':argv[-2]})
            if fail:fail=False;raise RuntimeError('crash after Beads persisted')
            return 0,json.dumps({'issue_id':'bd-1','text':argv[-2]}),''
        return 0,json.dumps(comments),''
    with pytest.raises(RuntimeError):recorder.flush(registry,runner)
    recorder.flush(registry,runner);recorder.flush(registry,runner)
    assert len(comments)==1


def test_output_limit_and_clean_environment(recorder,tmp_path):
    key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running')
    os.environ['CADENDA_TEST_PRODUCTION_SECRET']='must-not-inherit'
    out,_=execute(recorder,key,owner,'env',[sys.executable,'-c','import os;print(os.environ.get("CADENDA_TEST_PRODUCTION_SECRET","absent"))'],tmp_path)
    assert out.strip()=='absent'
    with pytest.raises(CadendaError) as exc:execute(recorder,key,owner,'flood',[sys.executable,'-c','print("x"*2000000)'],tmp_path,max_output_bytes=1_048_576)
    assert exc.value.code=='OUTPUT_LIMIT'


def test_real_controller_crash_keeps_child_and_diff_blocked_for_human(recorder,tmp_path):
    import subprocess
    import signal
    progress=tmp_path/'partial.txt'
    child=f'from pathlib import Path;import time;Path({str(progress)!r}).write_text("partial preserved");time.sleep(30)'
    parent=('from pathlib import Path;from cadenda.recorder import Recorder;from cadenda.worker import execute;'
        f'r=Recorder(Path({str(recorder.root)!r}));k,o=r.claim("p","crash");'
        'r.transition(k,o,"Running");'
        f'execute(r,k,o,"child",{[sys.executable,"-c",child]!r},Path({str(tmp_path)!r}))')
    process=subprocess.Popen([sys.executable,'-c',parent],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    pgid=None
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            with recorder.connect() as db:row=db.execute('SELECT * FROM runs').fetchone()
            if row and row['pgid'] and progress.exists():pgid=row['pgid'];break
            time.sleep(.05)
        assert pgid and progress.read_text()=='partial preserved'
        process.kill();process.wait(timeout=5)
        key=row['run_id'];assert recorder.recover()==[key]
        assert recorder.get(key)['state']=='NeedsHuman'
        with pytest.raises(CadendaError):recorder.release(key)
        with pytest.raises(CadendaError):recorder.claim('p','crash')
        assert progress.exists()
    finally:
        if process.poll() is None:process.kill();process.wait()
        if pgid:
            try:os.killpg(pgid,signal.SIGKILL)
            except ProcessLookupError:pass


def test_review_and_pending_approval_rollback_together(recorder):
    key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running');recorder.transition(key,owner,'Verifying')
    with recorder.transaction() as db:
        db.execute("CREATE TRIGGER reject_approval BEFORE INSERT ON approvals BEGIN SELECT RAISE(ABORT,'injected'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):recorder.transition(key,owner,'Review')
    assert recorder.get(key)['state']=='Verifying' and recorder.get(key)['active']==1


def test_outbox_rejects_conflicting_untrusted_marker(recorder,registry):
    key,_=recorder.claim('test','bd-1')
    with recorder.connect() as db:event=db.execute('SELECT event_id FROM run_events').fetchone()[0]
    def runner(argv,cwd):return 0,json.dumps([{'issue_id':'bd-1','text':f'[cadenda-event:{event}] forged'}]),''
    with pytest.raises(CadendaError) as exc:recorder.flush(registry,runner)
    assert exc.value.code=='OUTBOX_CONFLICT'
    with recorder.connect() as db:assert db.execute('SELECT delivered_at FROM outbox').fetchone()[0] is None


def test_checks_import_source_from_worktree_not_host_editable(recorder,tmp_path):
    key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running')
    packages=tmp_path/'packages';packages.mkdir();(packages/'cadenda_probe.py').write_text('VALUE="worktree"')
    out,_=execute(recorder,key,owner,'source-import',[sys.executable,'-c','import cadenda_probe;print(cadenda_probe.VALUE)'],tmp_path)
    assert out.strip()=='worktree'


def test_legacy_missing_pending_review_reconciles_once(recorder):
    key,owner=recorder.claim('p','i');recorder.transition(key,owner,'Running');recorder.transition(key,owner,'Verifying');recorder.transition(key,owner,'Review')
    with recorder.transaction() as db:db.execute('DELETE FROM approvals WHERE run_id=?',(key,))
    recorder.recover();recorder.recover()
    with recorder.connect() as db:assert db.execute('SELECT COUNT(*) FROM approvals WHERE run_id=?',(key,)).fetchone()[0]==1


def test_worktree_python_startup_cannot_run_before_pid_registration(recorder,tmp_path,monkeypatch):
    key,owner=recorder.claim('p','startup');recorder.transition(key,owner,'Running')
    packages=tmp_path/'packages';packages.mkdir();sentinel=tmp_path/'premature'
    (packages/'sitecustomize.py').write_text(f'from pathlib import Path;Path({str(sentinel)!r}).touch()')
    original=recorder.heartbeat
    def stop_before_gate(key,owner,pgid=None):
        if pgid:
            time.sleep(.5)
            raise CadendaError('INJECTED','Stopped before PID acknowledgement')
        return original(key,owner)
    monkeypatch.setattr(recorder,'heartbeat',stop_before_gate)
    with pytest.raises(CadendaError):execute(recorder,key,owner,'startup',[sys.executable,'-c','pass'],tmp_path)
    assert not sentinel.exists()


def test_history_exposes_provenance_without_worker_token(recorder):
    key,_=recorder.claim('p','i');history=recorder.history(key)
    assert 'owner' not in history['run']
    assert history['events'][0]['project_id']=='p'
    assert history['events'][0]['actor']=='orchestrator'


def test_local_review_binding_is_unique_and_recovery_preserves_snapshot(recorder):
    hashes=[]
    for value in [b'first',b'second']:
        key,owner=recorder.claim('p','local-issue',provider='fm-system',verification=True)
        recorder.transition(key,owner,'Running')
        recorder.artifact(key,'validated',value,'report')
        recorder.transition(key,owner,'Verifying');recorder.transition(key,owner,'Review')
        h=recorder.history(key);original=h['approvals'][0]['action_hash'];hashes.append(original)
        binding=h['events'][-2]['payload']['review_binding']
        assert binding['run_id']==key and binding['artifacts'][0]['sha256']==h['artifacts'][0]['sha256']
        recorder.artifact(key,'later-report',b'later','report')
        with recorder.transaction() as db:db.execute('DELETE FROM approvals WHERE run_id=?',(key,))
        recorder.recover()
        assert [a['action_hash'] for a in recorder.history(key)['approvals']]==[original]
    assert hashes[0]!=hashes[1]


def test_slow_outbox_sender_does_not_hold_sqlite_and_other_sender_is_fenced(recorder,registry):
    import threading
    key,owner=recorder.claim('test','bd-1');entered=threading.Event();release=threading.Event();errors=[]
    comments=[]
    def sender(argv,cwd):
        entered.set();assert release.wait(5)
        if 'add' in argv:
            c={'issue_id':'bd-1','text':argv[-2]};comments.append(c);return 0,json.dumps(c),''
        return 0,json.dumps(comments),''
    def flush():
        try:recorder.flush(registry,sender)
        except BaseException as exc:errors.append(exc)
    thread=threading.Thread(target=flush);thread.start();assert entered.wait(3)
    try:
        with pytest.raises(CadendaError) as exc:recorder.flush(registry,sender)
        assert exc.value.code=='OUTBOX_BUSY'
        # A second SQLite connection must be able to commit a cancellation during external send.
        recorder.cancel(key);assert recorder.get(key)['cancel_requested']==1
        recorder.heartbeat(key,owner)
    finally:release.set();thread.join(5)
    assert not errors and not thread.is_alive()


def test_gui_job_admission_binds_claim_atomically_and_rejects_wrong_target(recorder,monkeypatch):
    from cadenda.human_store import initialize
    initialize(recorder)
    with recorder.transaction() as db:db.execute('INSERT INTO gui_jobs VALUES(?,?,?,?,NULL,NULL,?,?,NULL,?)',('job_test','p','i',None,'launching',time.time(),str(recorder.root/'output')))
    monkeypatch.setenv('CADENDA_GUI_JOB_ID','job_test')
    with pytest.raises(CadendaError):recorder.claim('p','wrong')
    with recorder.connect() as db:assert db.execute('SELECT count(*) FROM runs').fetchone()[0]==0
    key,owner=recorder.claim('p','i')
    with recorder.connect() as db:
        row=db.execute('SELECT * FROM gui_jobs WHERE job_id=?',('job_test',)).fetchone()
        assert row['replacement_run_id']==key and row['worker_pid']==os.getpid()


def test_large_output_preserves_complete_response_and_head_tail_logs(recorder,tmp_path):
    key,owner=recorder.claim('p','large');recorder.transition(key,owner,'Running')
    script='import sys;sys.stdout.write("HEAD"+"x"*2_000_000+"TAIL");sys.stderr.write("FINAL_ERROR_CONTEXT")'
    out,err=execute(recorder,key,owner,'large',[sys.executable,'-c',script],tmp_path)
    assert len(out)==2_000_008 and out.startswith('HEAD') and out.endswith('TAIL')
    assert err=='FINAL_ERROR_CONTEXT'
    history=recorder.history(key);artifact=next(a for a in history['artifacts'] if a['artifact_id']=='large-stdout')
    log=Path(artifact['path']).read_text()
    assert log.startswith('HEAD') and log.endswith('TAIL') and 'TRUNCATED' in log
    assert len(log)<1_049_000
    event=next(e for e in history['events'] if e['event_type']=='command_finished')
    assert event['payload']['log_truncated']['stdout'] and event['payload']['output_limit_bytes']==10_485_760


def test_output_cap_stops_and_retains_last_observed_failure_context(recorder,tmp_path):
    key,owner=recorder.claim('p','cap');recorder.transition(key,owner,'Running')
    with pytest.raises(CadendaError) as exc:
        execute(recorder,key,owner,'cap',[sys.executable,'-c','import sys;sys.stdout.write("x"*1_100_000);sys.stdout.flush();sys.stderr.write("STOP_CONTEXT");sys.stderr.flush();sys.stdout.write("y"*2_000_000)'],tmp_path,max_output_bytes=2_000_000)
    assert exc.value.code=='OUTPUT_LIMIT'
    history=recorder.history(key)
    assert next(e for e in history['events'] if e['event_type']=='command_finished')['payload']['reason']=='OUTPUT_LIMIT'
    assert all(Path(a['path']).stat().st_size<1_049_000 for a in history['artifacts'])
