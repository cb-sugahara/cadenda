import json
from pathlib import Path
import subprocess
import sys
import pytest
from cadenda import staging
from cadenda.config import Check
from cadenda.context_builder import ContextBuilder
from cadenda.recorder import Recorder
from cadenda.security import canonical,digest
from cadenda.errors import CadendaError
from conftest import FakeBeads


@pytest.fixture
def candidate(registry,monkeypatch):
    p=registry.project('test');p.staging_enabled=True;p.recorder_enabled=True;p.worktree_roots=[p.root];p.artifact_roots=[registry.settings.state_root/'artifacts'];p.checks=[Check(check_id='unit',argv=[sys.executable,'check.py'])]
    (p.root/'.git').rmdir()
    def git(*args):return subprocess.check_output(['git','-C',str(p.root),*args],text=True).strip()
    git('init','--initial-branch=main');(p.root/'.beads').mkdir();(p.root/'.gitignore').write_text('.beads/\n');git('add','.gitignore');(p.root/'app.py').write_text('print("synthetic staging fixture")\n');git('add','app.py');git('-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','-m','Candidate')
    sha=git('rev-parse','HEAD');r=Recorder(registry.settings.state_root);key,owner=r.claim('test','bd-1',provider='antigravity+executor');r.transition(key,owner,'Running',worktree=str(p.root),base_commit=sha,commit_id=sha)
    r.artifact(key,'verification-tree',canonical({'tree':git('rev-parse','HEAD^{tree}'),'checks':{'unit':digest(canonical(p.checks[0].argv))}}),'report')
    builder=lambda reg:ContextBuilder(reg,adapter_factory=lambda p:FakeBeads())
    monkeypatch.setattr(staging,'ContextBuilder',builder)
    hashes=staging.requirements(builder(registry).build('test','bd-1',mode='review',budget_tokens=150000))
    r.artifact(key,'bound-review-fixture',canonical({'commit':sha,'review':{'verdict':'approved','synthetic_only':True},'requirement_hashes':hashes}),'review')
    r.transition(key,owner,'Verifying');r.transition(key,owner,'Review');approval=r.history(key)['approvals'][-1]
    with r.transaction() as db:db.execute("UPDATE approvals SET status='approved',actor_ref='owner:test' WHERE approval_id=?",(approval['approval_id'],));r._event(db,key,'human_review_decided',{'approval_id':approval['approval_id'],'verdict':'approve','actor_ref':'owner:test'})
    return registry,r,key,p


def test_local_stage_is_exact_nonexecuted_and_detects_later_tamper(candidate):
    registry,r,key,p=candidate;plan=staging.prepare(registry,key);result=staging.apply(registry,key,plan['fingerprint']);target=Path(result['target'])
    assert (target/'app.py').read_bytes()==(p.root/'app.py').read_bytes() and not result['source_executed']
    assert staging.apply(registry,key,plan['fingerprint'])['already_staged']
    (target/'app.py').write_text('changed')
    with pytest.raises(CadendaError) as exc:staging.apply(registry,key,plan['fingerprint'])
    assert exc.value.code=='STAGING_CORRUPT'


def test_staging_requires_explicit_grant_current_review_and_unchanged_requirements(candidate):
    registry,r,key,p=candidate;p.staging_enabled=False
    with pytest.raises(CadendaError):staging.prepare(registry,key)
    p.staging_enabled=True;(p.root/'untracked').write_text('new')
    with pytest.raises(CadendaError) as exc:staging.prepare(registry,key)
    assert exc.value.code=='STALE_EVIDENCE'


def test_staging_rechecks_review_in_publication_transaction(candidate,monkeypatch):
    registry,r,key,p=candidate;original=staging.prepare;plan=original(registry,key);calls=0
    def race(*args):
        nonlocal calls
        value=original(*args);calls+=1
        if calls==2:
            with r.transaction() as db:db.execute("UPDATE runs SET state='Verifying',active=1 WHERE run_id=?",(key,))
        return value
    monkeypatch.setattr(staging,'prepare',race)
    with pytest.raises(CadendaError) as exc:staging.apply(registry,key,plan['fingerprint'])
    assert exc.value.code=='STALE_STAGE' and not Path(plan['plan']['target']).exists()


def test_staging_reloads_owner_configuration_and_rejects_revocation(candidate,tmp_path):
    import yaml
    registry,r,key,p=candidate
    registry.config_path=tmp_path/'projects.yaml'
    registry.config_path.write_text(yaml.safe_dump(registry.settings.model_dump(mode='json')))
    plan=staging.prepare(registry,key)
    value=registry.settings.model_dump(mode='json');value['projects'][0]['staging_enabled']=False
    registry.config_path.write_text(yaml.safe_dump(value))
    with pytest.raises(CadendaError) as exc:staging.apply(registry,key,plan['fingerprint'])
    assert exc.value.code=='STAGING_DISABLED' and not Path(plan['plan']['target']).exists()


def test_staging_chooses_latest_review_event_not_lexical_id(candidate,monkeypatch):
    from conftest import comment
    registry,r,key,p=candidate
    factory=lambda reg:ContextBuilder(reg,adapter_factory=lambda _:FakeBeads(comments={'bd-1':[comment('new','Revised synthetic requirement')]}))
    monkeypatch.setattr(staging,'ContextBuilder',factory)
    hashes=staging.requirements(factory(registry).build('test','bd-1',mode='review',budget_tokens=150000))
    r.artifact(key,'bound-review-0000',canonical({'commit':r.get(key)['commit_id'],'review':{'verdict':'approved','synthetic_only':True},'requirement_hashes':hashes}),'review')
    with r.transaction() as db:
        binding=r._review_binding(db,r.get(key));action=digest(canonical(binding));approval='approval_latest'
        r._event(db,key,'state_changed',{'from':'Verifying','to':'Review','review_binding':binding})
        db.execute("INSERT INTO approvals(approval_id,run_id,action_hash,status) VALUES(?,?,?,'approved')",(approval,key,action))
        r._event(db,key,'approval_requested',{'approval_id':approval,'action_hash':action})
        r._event(db,key,'human_review_decided',{'approval_id':approval,'verdict':'approve','actor_ref':'owner:synthetic'})
    assert staging.prepare(registry,key)['plan']['requirement_hashes']==hashes


def test_outbox_delivery_waits_until_staging_publication(candidate,monkeypatch):
    import threading,time
    from cadenda.process import beads_guard
    registry,r,key,p=candidate;plan=staging.prepare(registry,key);original=staging.prepare;calls=0;threads=[];delivered=[]
    def sender(argv,cwd,**kwargs):
        with beads_guard(cwd):
            if 'add' in argv:
                delivered.append(True);return 0,json.dumps({'text':argv[-2],'issue_id':argv[-3]}),''
            return 0,'[]',''
    def race(*args):
        nonlocal calls
        result=original(*args);calls+=1
        if calls==2:
            thread=threading.Thread(target=lambda:r.flush(registry,sender));threads.append(thread);thread.start();time.sleep(.08)
            assert not delivered
        return result
    monkeypatch.setattr(staging,'prepare',race)
    result=staging.apply(registry,key,plan['fingerprint']);assert Path(result['target']).is_dir()
    threads[0].join(3);assert delivered and not threads[0].is_alive()
