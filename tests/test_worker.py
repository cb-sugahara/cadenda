from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import json

import pytest

from cadenda.config import Registry, Settings, Project, Check
from cadenda.errors import CadendaError
import cadenda.worker as module


@pytest.fixture
def worker(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir()
    def git(*args):subprocess.run(['git','-C',str(root),*args],capture_output=True,check=True)
    git('init');(root/'sample.txt').write_text('baseline')
    git('add','.');git('-c','user.name=Test','-c','user.email=test@localhost','commit','-m','baseline')
    state=tmp_path/'state'
    project=Project(project_id='test',root=root,worktree_roots=[tmp_path/'worktrees'],artifact_roots=[state/'artifacts'],documents=[],checks=[Check(check_id='test',argv=[sys.executable,'-c','print("independent check")'])])
    registry=Registry(Settings(state_root=state,projects=[project]))
    monkeypatch.setattr(module,'BeadsAdapter',lambda p:SimpleNamespace(issue=lambda key:(SimpleNamespace(status='open',dependencies=[],acceptance_criteria='test passes'),{})))
    monkeypatch.setattr(module,'ContextBuilder',lambda r:SimpleNamespace(build=lambda *args:SimpleNamespace(snapshot_id='ctx_test',warnings=[])))
    return module.Worker(registry),project


def test_real_worktree_check_pipeline_and_preservation(worker):
    runner,project=worker
    result=runner.start('test','bd-1',checks_only=True)
    assert result['state']=='Review'
    assert Path(result['worktree']).exists() and Path(result['worktree'])!=project.root
    assert (project.root/'sample.txt').read_text()=='baseline'
    assert runner.recorder.catalog_runs('test')[0].artifacts


def test_independent_test_failure_cannot_reach_review(worker):
    runner,project=worker
    project.checks=[Check(check_id='fail',argv=[sys.executable,'-c','raise SystemExit(1)'])]
    with pytest.raises(CadendaError):runner.start('test','bd-1',checks_only=True)
    with runner.recorder.connect() as db:row=db.execute('SELECT * FROM runs').fetchone()
    assert row['state']=='RetryableFailure' and Path(row['worktree']).exists()


def test_zero_exit_soft_denial_stays_needs_human(worker,monkeypatch):
    runner,project=worker
    monkeypatch.setattr(module,'SnapshotStore',lambda root:SimpleNamespace(load=lambda key:SimpleNamespace(markdown=lambda:'context')))
    monkeypatch.setattr(module,'execute',lambda *args,**kwargs:(json.dumps({'status':'SUCCESS','denied_actions':[{'action':'write_file'}],'structured_output':{'status':'implemented','summary':'pretends success'}}),''))
    with pytest.raises(CadendaError) as exc:runner.start('test','bd-1')
    assert exc.value.code=='AGENT_DENIED'
    with runner.recorder.connect() as db:row=db.execute('SELECT * FROM runs').fetchone()
    assert row['state']=='NeedsHuman'


def test_dirty_source_is_not_touched(worker):
    runner,project=worker
    (project.root/'sample.txt').write_text('human edits')
    with pytest.raises(CadendaError) as exc:runner.start('test','bd-1',checks_only=True)
    assert exc.value.code=='DIRTY_REPOSITORY'
    assert (project.root/'sample.txt').read_text()=='human edits'
