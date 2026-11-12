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
    project=Project(project_id='test',root=root,cloud_enabled=True,worktree_roots=[tmp_path/'worktrees'],artifact_roots=[state/'artifacts'],documents=[],checks=[Check(check_id='test',argv=[sys.executable,'-c','print("independent check")'])])
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


@pytest.mark.parametrize('scenario',['approve','tamper','stale','invalid'])
def test_phase4_review_binds_checks_and_rejects_stale_evidence(worker,monkeypatch,scenario):
    import cadenda.pipeline as pipeline_module
    from cadenda.pipeline import Pipeline
    from cadenda.intelligence import Review
    runner,project=worker;row=runner.start('test','bd-1',checks_only=True)
    with runner.recorder.transaction() as db:db.execute("UPDATE runs SET provider='antigravity+executor' WHERE run_id=?",(row['run_id'],))
    monkeypatch.setattr(pipeline_module,'BeadsAdapter',lambda p:SimpleNamespace(issue=lambda key:(SimpleNamespace(revision='1'),[{'id':key,'revision':'1'}])))
    monkeypatch.setattr(pipeline_module,'ContextBuilder',lambda r:SimpleNamespace(build=lambda *a,**k:SimpleNamespace(warnings=[],sources=[],markdown=lambda:'reference context')))
    called=[]
    def model(*args):
        called.append(args)
        if scenario=='invalid':raise CadendaError('CODEX_INVALID','bad response')
        if scenario=='stale':(Path(row['worktree'])/'sample.txt').write_text('changed during review')
        return Review(verdict='approved',findings=[],requirements_checked=['AC'],source_refs=['issue'])
    monkeypatch.setattr(pipeline_module,'CodexAdapter',lambda r:SimpleNamespace(run=model))
    if scenario=='tamper':
        artifact=runner.recorder.history(row['run_id'])['artifacts'][0];Path(artifact['path']).write_text('corrupt')
    pipeline=Pipeline(runner.registry)
    if scenario=='approve':
        assert pipeline.review(row['run_id'])['review']['verdict']=='approved'
        assert called[0][-1]['independent_checks']
    else:
        with pytest.raises(CadendaError):pipeline.review(row['run_id'])
        if scenario=='tamper':assert not called
        else:assert pipeline.recorder.get(row['run_id'])['state']=='NeedsHuman'


def test_phase4_repairs_preserve_prior_commit_and_pass_feedback(worker,monkeypatch):
    import cadenda.pipeline as pipeline_module
    from cadenda.pipeline import Pipeline
    from cadenda.intelligence import Review
    runner,project=worker;real_execute=module.execute;prompts=[];reviews=[]
    monkeypatch.setattr(module,'SnapshotStore',lambda root:SimpleNamespace(load=lambda key:SimpleNamespace(markdown=lambda:'Add answer and README')))
    def developer(rec,key,owner,name,argv,cwd,*args,**kwargs):
        if name!='agent':return real_execute(rec,key,owner,name,argv,cwd,*args,**kwargs)
        prompts.append(argv[-1])
        (cwd/('answer.txt' if len(prompts)==1 else 'README.md')).write_text('implemented')
        return json.dumps({'status':'SUCCESS','structured_output':{'status':'implemented','summary':'edited'}}),''
    monkeypatch.setattr(module,'execute',developer)
    monkeypatch.setattr(pipeline_module,'ContextBuilder',lambda r:SimpleNamespace(build=lambda *a,**k:SimpleNamespace(warnings=[],sources=[],markdown=lambda:'answer and README')))
    monkeypatch.setattr(pipeline_module,'BeadsAdapter',lambda p:SimpleNamespace(issue=lambda key:(SimpleNamespace(revision='1'),[{'id':key,'revision':'1'}])))
    def reviewer(*args):
        reviews.append(args)
        return Review(verdict='changes_requested' if len(reviews)==1 else 'approved',findings=['README missing'] if len(reviews)==1 else [],requirements_checked=['AC'],source_refs=['issue'])
    monkeypatch.setattr(pipeline_module,'CodexAdapter',lambda r:SimpleNamespace(run=reviewer))
    result=Pipeline(runner.registry).run('test','bd-1')
    assert len(prompts)==2 and 'README missing' in prompts[1]
    with runner.recorder.connect() as db:rows=db.execute('SELECT * FROM runs ORDER BY created_at').fetchall()
    assert rows[1]['base_commit']==rows[0]['base_commit']
    assert (Path(rows[1]['worktree'])/'answer.txt').exists()
    assert result['human_approval_required']


def test_checks_that_mutate_source_are_not_approved(worker):
    runner,project=worker
    project.checks.append(Check(check_id='mutator',argv=[sys.executable,'-c','from pathlib import Path;Path("sample.txt").write_text("changed after earlier check")']))
    with pytest.raises(CadendaError) as exc:runner.start('test','bd-1',checks_only=True)
    assert exc.value.code=='SOURCE_CHANGED'


def test_changed_related_requirement_hash_invalidates_review(worker,monkeypatch):
    import cadenda.pipeline as pipeline_module
    from cadenda.pipeline import Pipeline
    from cadenda.intelligence import Review
    runner,project=worker;row=runner.start('test','bd-1',checks_only=True)
    with runner.recorder.transaction() as db:db.execute("UPDATE runs SET provider='antigravity+executor' WHERE run_id=?",(row['run_id'],))
    calls=[]
    def context(*args,**kwargs):
        calls.append(True)
        return SimpleNamespace(warnings=[],markdown=lambda:'context',sources=[SimpleNamespace(ref='beads://test/related/comments/c1',raw_sha256='before' if len(calls)==1 else 'changed')])
    monkeypatch.setattr(pipeline_module,'ContextBuilder',lambda r:SimpleNamespace(build=context))
    monkeypatch.setattr(pipeline_module,'BeadsAdapter',lambda p:SimpleNamespace(issue=lambda key:(SimpleNamespace(revision='1'),[{'id':key}])))
    monkeypatch.setattr(pipeline_module,'CodexAdapter',lambda r:SimpleNamespace(run=lambda *a:Review(verdict='approved',findings=[],requirements_checked=['AC'],source_refs=['issue'])))
    with pytest.raises(CadendaError) as exc:Pipeline(runner.registry).review(row['run_id'])
    assert exc.value.code=='STALE_REQUIREMENTS'
