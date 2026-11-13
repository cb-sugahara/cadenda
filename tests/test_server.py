import json
from pathlib import Path
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from cadenda.beads_adapter import Issue,Comment
from cadenda.server import create_app
from cadenda.human_store import issue_fingerprint,receipts
from cadenda.context_builder import ContextBuilder
from cadenda.security import canonical,digest
from cadenda.recorder import Recorder
from cadenda.config import Registry,Settings
from conftest import row,git_runner

ORIGIN='http://127.0.0.1:8765'

class MemoryBeads:
    def __init__(self):
        self.rows={'bd-1':row()};self.messages={'bd-1':[]};self.calls=[]
    def adapter(self,project):return self
    def issue(self,key):return Issue.model_validate(self.rows[key]),[self.rows[key]]
    def comments(self,key):
        raw=self.messages.get(key,[]);return [Comment.model_validate(c) for c in raw],raw
    def _json(self,*args):return list(self.rows.values())
    def write(self,argv,cwd):
        self.calls.append(argv);args=argv[argv.index('--json')+1:]
        if args[0]=='create':
            fields={k:next(a.split('=',1)[1] for a in args if a.startswith('--'+flag+'=')) for k,flag in [('title','title'),('description','description'),('acceptance_criteria','acceptance')]}
            key='bd-'+str(len(self.rows)+1);self.rows[key]=row(key,**fields);self.messages[key]=[];return 0,json.dumps(self.rows[key]),''
        if args[0]=='update':
            key=args[1]
            for field,flag in [('title','title'),('description','description'),('acceptance_criteria','acceptance')]:self.rows[key][field]=next(a.split('=',1)[1] for a in args if a.startswith('--'+flag+'='))
            return 0,json.dumps([self.rows[key]]),''
        assert args[:3]==['comments','add','--']
        key,text=args[3:];c={'id':len(self.messages[key])+1,'issue_id':key,'author':'fixture-owner','text':text,'created_at':'2026-11-13T00:00:00Z'};self.messages[key].append(c);return 0,json.dumps(c),''

@pytest.fixture
def gui(registry):
    registry.config_path=registry.settings.state_root.parent/'config.yaml'
    registry.settings.projects[0].recorder_enabled=True
    (registry.settings.projects[0].root/'.beads').mkdir()
    fake=MemoryBeads();launches=[]
    def launcher(*args,**kwargs):
        launches.append((args,kwargs));return SimpleNamespace(pid=99999999,poll=lambda:0)
    app=create_app(registry,pairing_code='fixture-pairing',adapter_factory=fake.adapter,writer=fake.write,launcher=launcher,context_runner=git_runner)
    with TestClient(app,base_url=ORIGIN) as client:
        client.headers['origin']=ORIGIN
        yield app,client,fake,registry,launches

def login(client,code='fixture-pairing'):
    result=client.post('/api/session/login',json={'code':code});assert result.status_code==200,result.text
    client.headers['x-cadenda-csrf']=result.json()['csrf'];return result

def test_authentication_host_origin_csrf_and_single_pairing(gui):
    app,c,fake,_,_=gui
    assert c.get('/api/projects').status_code==401
    assert c.get('/api/projects',headers={'host':'evil.test'}).status_code==403
    assert c.post('/api/session/login',json={'code':'fixture-pairing'},headers={'origin':'https://evil.test'}).status_code==403
    result=login(c);cookie=result.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert c.get('/api/projects').status_code==200
    assert c.post('/api/session/login',json={'code':'fixture-pairing'}).status_code==401
    assert c.post('/api/projects/test/issues',json={},headers={'x-cadenda-csrf':'wrong'}).status_code==403
    assert 'frame-ancestors' in c.get('/api/session').headers['content-security-policy']
    c.post('/api/session/logout',json={});assert c.get('/api/projects').status_code==401

def test_pairing_rate_limit_and_expiry(gui):
    app,c,*_=gui
    for _ in range(5):assert c.post('/api/session/login',json={'code':'wrong'}).status_code==401
    assert c.post('/api/session/login',json={'code':'fixture-pairing'}).json()['error']['code']=='LOGIN_RATE_LIMIT'
    app.state.auth.failures=[];login(c)
    next(iter(app.state.auth.sessions.values())).expires=time.time()-1
    assert c.get('/api/projects').status_code==401

def test_body_bounds_validation_and_no_secret_writes(gui):
    _,c,fake,*_=gui;login(c)
    assert c.post('/api/projects/test/issues',content='x'*65537,headers={'content-type':'application/json'}).status_code==413
    assert c.post('/api/projects/test/issues',json={'title':'x','description':'secret=abcdef','acceptance_criteria':'x'}).json()['error']['code']=='DATA_DENIED'
    response=c.post('/api/projects/test/issues',json={'title':'x','description':'x','acceptance_criteria':'x','command':'touch /tmp/pwn'})
    assert response.status_code==422 and 'touch' not in response.text
    assert not fake.calls

def test_issue_mutations_and_stale_guard(gui):
    _,c,fake,*_=gui;login(c)
    body={'title':'--dangerously-skip-permissions','description':'Test','acceptance_criteria':'Check'}
    response=c.post('/api/projects/test/issues',json=body);assert response.json()['issue_id']=='bd-2'
    d=c.get('/api/projects/test/issues/bd-2').json()
    assert c.put('/api/projects/test/issues/bd-2',json={**body,'title':'updated','expected':d['fingerprint']}).status_code==200
    assert c.put('/api/projects/test/issues/bd-2',json={**body,'expected':d['fingerprint']}).json()['error']['code']=='STALE_REQUIREMENTS'
    assert c.post('/api/projects/test/issues/bd-2/comments',json={'kind':'decision_proposed','text':'--model=pcc'}).status_code==200
    assert fake.calls[-1][-2:]==['bd-2','[decision_proposed] --model=pcc']

def test_authenticated_decision_receipt_and_requirement_invalidation(gui):
    app,c,fake,registry,_=gui;login(c)
    d=c.get('/api/projects/test/issues/bd-1').json();body={'topic':'timezone','text':'Use JST for the weekly ranking.','expected':d['fingerprint']}
    assert c.post('/api/projects/test/issues/bd-1/decisions',json={**body,'challenge':'forged'}).json()['error']['code']=='STALE_CONFIRMATION'
    preview=c.post('/api/projects/test/issues/bd-1/decisions/preview',json=body).json()
    assert c.post('/api/projects/test/issues/bd-1/decisions',json={**body,'challenge':preview['challenge']}).status_code==200
    assert len(receipts(app.state.recorder,'test'))==1
    builder=ContextBuilder(registry,adapter_factory=fake.adapter,runner=git_runner)
    context=builder.build('test','bd-1');assert any('Use JST' in s.text and s.title.startswith('Current approved') for s in context.sections)
    fake.rows['bd-1']['description']='Changed after decision'
    context=builder.build('test','bd-1');assert any(w.code=='DECISION_UNVERIFIED' for w in context.warnings)
    assert c.post('/api/projects/test/issues/bd-1/decisions',json={**body,'challenge':preview['challenge']}).status_code==409

def review_run(recorder):
    key,owner=recorder.claim('test','bd-1',provider='fm-system',verification=True);recorder.transition(key,owner,'Running');recorder.artifact(key,'validated',b'candidate','report');recorder.transition(key,owner,'Verifying');recorder.transition(key,owner,'Review')
    return key,recorder.history(key)['approvals'][-1]['approval_id']

def test_approval_binds_artifacts_current_requirements_and_one_time_confirmation(gui):
    app,c,fake,*_=gui;login(c);key,approval=review_run(app.state.recorder)
    preview=c.get('/api/approvals/'+approval).json()
    fake.rows['bd-1']['description']='New requirement'
    path='/api/approvals/'+approval+'/approve';body={'challenge':preview['challenge'],'fingerprint':preview['fingerprint']}
    assert c.post(path,json=body).json()['error']['code']=='STALE_APPROVAL'
    preview=c.get('/api/approvals/'+approval).json();body={k:preview[k] for k in ['challenge','fingerprint']}
    assert c.post(path,json=body).json()=={'status':'approved','execution_authorized':False}
    assert c.post(path,json=body).status_code==409
    assert app.state.recorder.history(key)['approvals'][0]['status']=='approved'
    assert app.state.recorder.get(key)['state']=='Review'

def test_tampered_artifact_blocks_read_and_approval(gui):
    app,c,*_=gui;login(c);key,approval=review_run(app.state.recorder)
    Path(app.state.recorder.history(key)['artifacts'][0]['path']).write_text('tampered')
    assert c.get('/api/approvals/'+approval).json()['error']['code']=='ARTIFACT_CORRUPT'
    assert c.get('/api/runs/'+key+'/artifacts/validated').json()['error']['code']=='ARTIFACT_CORRUPT'

def test_expired_confirmation_and_approval_cannot_be_used(gui):
    app,c,*_=gui;login(c);key,approval=review_run(app.state.recorder)
    preview=c.get('/api/approvals/'+approval).json();app.state.auth.challenges[preview['challenge']]['expires']=time.time()-1
    assert c.post('/api/approvals/'+approval+'/approve',json={k:preview[k] for k in ['challenge','fingerprint']}).json()['error']['code']=='STALE_CONFIRMATION'
    with app.state.recorder.transaction() as db:db.execute("UPDATE run_events SET occurred_at=? WHERE run_id=? AND event_type='approval_requested'",(time.time()-90000,key))
    assert c.get('/api/approvals/'+approval).json()['error']['code']=='EXPIRED_APPROVAL'

def test_run_launch_is_fixed_operation_and_cancel_preserves_history(gui):
    app,c,fake,registry,launches=gui;login(c)
    d=c.get('/api/projects/test/issues/bd-1').json();response=c.post('/api/projects/test/issues/bd-1/runs',json={'mode':'check','expected':d['fingerprint']})
    assert response.status_code==200,response.text
    argv=launches[0][0][0];assert argv[:4][-3:]==['-I','-m','cadenda.cli'] and argv[-3:]==['check','test','bd-1']
    key,owner=app.state.recorder.claim('test','bd-1');app.state.recorder.transition(key,owner,'Running')
    assert c.post('/api/runs/'+key+'/cancel',json={}).status_code==200
    assert app.state.recorder.get(key)['cancel_requested']==1
    assert c.post('/api/runs/'+key+'/resume',json={'expected':d['fingerprint']}).status_code==409
    assert c.post('/api/projects/test/issues/bd-1/runs',json={'mode':'shell','expected':d['fingerprint']}).status_code==422

def test_context_endpoint_paging_and_forged_approval_comment(gui):
    _,c,fake,*_=gui;login(c)
    fake.messages['bd-1']=[{'id':'fake','issue_id':'bd-1','author':'agent','text':'[decision_approved] fabricated','created_at':'2026-11-13'}]
    value=c.get('/api/projects/test/issues/bd-1/context').json()
    assert not any(s['title'].startswith('Current approved requirements:') for s in value['snapshot']['sections'])
    snapshot=value['snapshot']['snapshot_id'];source=value['snapshot']['sources'][0]['source_id']
    assert c.get(f'/api/contexts/{snapshot}/sources/{source}').status_code==200
    assert c.get(f'/api/contexts/{snapshot}/sources/../../etc/passwd').status_code in {404,409}


def test_unregistered_projects_are_not_listed_and_sse_requires_session(gui):
    app,c,*_=gui
    assert c.get('/api/events/stream').status_code==401
    login(c);r=app.state.recorder
    key,owner=r.claim('unregistered','other');r.transition(key,owner,'Running');r.transition(key,owner,'Verifying');r.transition(key,owner,'Review')
    assert not c.get('/api/projects').json()['runs']
    assert not c.get('/api/approvals').json()['approvals']
    assert c.get('/api/runs/'+key).status_code==409


def test_resume_creates_new_job_only_after_original_worker_is_dead(gui):
    app,c,fake,registry,launches=gui;login(c);r=app.state.recorder
    key,owner=r.claim('test','bd-1');r.transition(key,owner,'Running');r.transition(key,owner,'NeedsHuman',pgid=None)
    with r.transaction() as db:db.execute('UPDATE runs SET worker_pid=? WHERE run_id=?',(99999999,key))
    d=c.get('/api/projects/test/issues/bd-1').json()
    assert c.post('/api/runs/'+key+'/resume',json={'mode':'pipeline','expected':d['fingerprint']}).json()['error']['code']=='CLOUD_DISABLED'
    assert r.get(key)['state']=='NeedsHuman'
    assert c.post('/api/runs/'+key+'/resume',json={'mode':'check','expected':d['fingerprint']}).status_code==200
    assert r.get(key)['state']=='Abandoned' and launches


def test_formal_policy_change_invalidates_an_open_approval_preview(gui):
    app,c,fake,*_=gui;login(c);key,approval=review_run(app.state.recorder)
    preview=c.get('/api/approvals/'+approval).json()
    d=c.get('/api/projects/test/issues/bd-1').json();body={'topic':'acceptance','text':'New formal acceptance policy','expected':d['fingerprint']}
    decision=c.post('/api/projects/test/issues/bd-1/decisions/preview',json=body).json()
    assert c.post('/api/projects/test/issues/bd-1/decisions',json={**body,'challenge':decision['challenge']}).status_code==200
    assert c.post('/api/approvals/'+approval+'/approve',json={k:preview[k] for k in ['challenge','fingerprint']}).json()['error']['code']=='STALE_APPROVAL'
    assert c.get('/api/approvals/'+approval).json()['target']['requirements']['receipts']


def test_request_read_deadline_and_metadata_rejection_before_receive():
    import asyncio
    from cadenda.server import RequestLimit
    from cadenda.gui_auth import OwnerAuth
    async def run():
        called=[];sent=[]
        async def downstream(*args):pytest.fail('must not reach application')
        async def receive():called.append(True);await asyncio.sleep(1)
        async def send(value):sent.append(value)
        middleware=RequestLimit(downstream,ORIGIN,OwnerAuth('code'),body_timeout=.01)
        scope={'type':'http','method':'POST','path':'/api/session/login','scheme':'http','server':('127.0.0.1',8765),'client':('127.0.0.1',1234),'query_string':b'','headers':[(b'host',b'evil.test'),(b'origin',ORIGIN.encode()),(b'content-type',b'application/json')]}
        await middleware(scope,receive,send);assert not called and sent[0]['status']==403
        sent.clear();scope['headers'][0]=(b'host',b'127.0.0.1:8765')
        await middleware(scope,receive,send);assert sent[0]['status']==408
    asyncio.run(run())


def test_initial_run_state_and_sse_cursor_do_not_skip_a_concurrent_transition(gui,monkeypatch):
    app,c,*_=gui;login(c);r=app.state.recorder;key,owner=r.claim('test','bd-1')
    original=r.connect;injected=False
    def traced_connect():
        db=original()
        def trace(sql):
            nonlocal injected
            if not injected and 'COALESCE(MAX(rowid)' in sql:
                injected=True;r.transition(key,owner,'Running')
        db.set_trace_callback(trace);return db
    monkeypatch.setattr(r,'connect',traced_connect)
    response=c.get('/api/projects').json()
    with original() as db:newest=db.execute('SELECT MAX(rowid) FROM run_events').fetchone()[0]
    assert injected
    row=next(row for row in response['runs'] if row['run_id']==key)
    assert (row['state']=='Running' and response['cursor']>=newest) or (row['state']=='Claimed' and response['cursor']<newest)


def test_option_shaped_local_text_is_a_positional_operand(gui):
    app,c,fake,registry,launches=gui;login(c);registry.project('test').fm_local_enabled=True
    d=c.get('/api/projects/test/issues/bd-1').json()
    result=c.post('/api/projects/test/issues/bd-1/runs',json={'mode':'local','task':'classify','text':'--model=pcc','expected':d['fingerprint']})
    assert result.status_code==200
    assert launches[0][0][0][-3:]==['classify','--','--model=pcc']


def test_sse_rejects_out_of_range_cursor_without_opening_stream(gui):
    _,c,*_=gui;login(c)
    assert c.get('/api/events/stream?after=999999999999999999999999999999999').status_code==422
    assert c.get('/api/events/stream',headers={'last-event-id':'999999999999999999999999999999999'}).json()['error']['code']=='INVALID_CURSOR'


def test_approval_preview_redacts_existing_requirement_and_policy_secrets(gui):
    from datetime import datetime,timezone
    from cadenda.config import DecisionReceipt
    app,c,fake,*_=gui;login(c)
    fake.rows['bd-1']['description']='Existing password=legacy_password_value'
    text='[decision_approved] Existing api_key=legacy_api_key_value'
    fake.messages['bd-1']=[{'id':'legacy','issue_id':'bd-1','author':'owner','text':text,'created_at':'2026-11-13'}]
    receipt=DecisionReceipt(topic='policy',issue_id='bd-1',comment_id='legacy',comment_sha256=digest(text.encode()),applies_to=['bd-1'],approved_at=datetime.now(timezone.utc),approval_ref='owner')
    with app.state.recorder.transaction() as db:db.execute('INSERT INTO human_receipts VALUES(?,?,?)',('test',receipt.model_dump_json(),'owner'))
    key,approval=review_run(app.state.recorder)
    response=c.get('/api/approvals/'+approval)
    assert response.status_code==200 and 'legacy_password_value' not in response.text and 'legacy_api_key_value' not in response.text
    body={k:response.json()[k] for k in ['challenge','fingerprint']}
    assert c.post('/api/approvals/'+approval+'/approve',json=body).status_code==200


def test_gui_restart_recovers_dead_worker_and_owner_can_resume_without_cli(gui):
    app,c,fake,registry,_=gui;r=app.state.recorder
    key,owner=r.claim('test','bd-1');r.transition(key,owner,'Running')
    with r.transaction() as db:db.execute('UPDATE runs SET worker_pid=NULL WHERE run_id=?',(key,))
    launched=[]
    def launcher(*args,**kwargs):launched.append(args);return SimpleNamespace(pid=99999999,poll=lambda:0)
    restarted=create_app(registry,pairing_code='fixture-pairing',adapter_factory=fake.adapter,writer=fake.write,launcher=launcher,context_runner=git_runner)
    assert r.get(key)['state']=='NeedsHuman'
    with TestClient(restarted,base_url=ORIGIN) as client:
        client.headers['origin']=ORIGIN;login(client)
        assert client.post('/api/recovery',json={}).json()['processes_killed'] is False
        d=client.get('/api/projects/test/issues/bd-1').json()
        assert client.post('/api/runs/'+key+'/resume',json={'expected':d['fingerprint']}).status_code==200
    assert launched and r.get(key)['state']=='Abandoned'


def test_resume_conflict_keeps_original_and_launcher_failure_is_retryable(gui):
    app,c,fake,registry,_=gui;r=app.state.recorder;key,owner=r.claim('test','bd-1');r.transition(key,owner,'Running');r.transition(key,owner,'NeedsHuman')
    with r.transaction() as db:db.execute('UPDATE runs SET worker_pid=NULL WHERE run_id=?',(key,))
    no_config=Registry(registry.settings)
    unavailable=create_app(no_config,pairing_code='fixture-pairing',adapter_factory=fake.adapter,writer=fake.write,context_runner=git_runner)
    with TestClient(unavailable,base_url=ORIGIN) as missing:
        missing.headers['origin']=ORIGIN;login(missing);d=missing.get('/api/projects/test/issues/bd-1').json()
        assert missing.post('/api/runs/'+key+'/resume',json={'expected':d['fingerprint']}).json()['error']['code']=='CONFIG_REQUIRED'
        assert r.get(key)['state']=='NeedsHuman'
    fail=True
    def launcher(*args,**kwargs):
        if fail:raise OSError('injected startup failure')
        return SimpleNamespace(pid=99999999,poll=lambda:0)
    restarted=create_app(registry,pairing_code='fixture-pairing',adapter_factory=fake.adapter,writer=fake.write,launcher=launcher,context_runner=git_runner)
    with TestClient(restarted,base_url=ORIGIN) as client:
        client.headers['origin']=ORIGIN;login(client);d=client.get('/api/projects/test/issues/bd-1').json();body={'expected':d['fingerprint']}
        restarted.state.jobs['pending']={'process':SimpleNamespace(pid=99999998,poll=lambda:None),'project_id':'test','issue_id':'other'}
        assert client.post('/api/runs/'+key+'/resume',json=body).json()['error']['code']=='RUN_CONFLICT'
        assert r.get(key)['state']=='NeedsHuman'
        restarted.state.jobs.clear()
        assert client.post('/api/runs/'+key+'/resume',json=body).json()['error']['code']=='LAUNCH_FAILED'
        assert r.get(key)['state']=='NeedsHuman' and r.get(key)['active']==1
        assert any(e['event_type']=='gui_resume_launch_failed' for e in r.history(key)['events'])
        fail=False
        assert client.post('/api/runs/'+key+'/resume',json=body).status_code==200
        assert r.get(key)['state']=='Abandoned'


def test_confirming_new_policy_invalidates_other_open_policy_previews(gui):
    app,c,fake,registry,_=gui;login(c);d=c.get('/api/projects/test/issues/bd-1').json()
    first={'topic':'policy','text':'First preview','expected':d['fingerprint']};second={**first,'text':'Confirmed newer policy'}
    p1=c.post('/api/projects/test/issues/bd-1/decisions/preview',json=first).json();p2=c.post('/api/projects/test/issues/bd-1/decisions/preview',json=second).json()
    assert c.post('/api/projects/test/issues/bd-1/decisions',json={**second,'challenge':p2['challenge']}).status_code==200
    assert c.post('/api/projects/test/issues/bd-1/decisions',json={**first,'challenge':p1['challenge']}).json()['error']['code']=='STALE_CONFIRMATION'
    p3=c.post('/api/projects/test/issues/bd-1/decisions/preview',json=first).json()
    assert p3['decision']['current_requirements']['receipts'][0]['comment']['text'].startswith('[decision_approved]')
    assert c.post('/api/projects/test/issues/bd-1/decisions',json={**first,'challenge':p3['challenge']}).status_code==200
    context=ContextBuilder(registry,adapter_factory=fake.adapter,runner=git_runner).build('test','bd-1')
    assert not any(w.code=='UNTRUSTED_APPROVAL_TAG' for w in context.warnings)
    assert any(s.title=='Current approved requirements: policy' and 'First preview' in s.text for s in context.sections)


def test_child_exit_before_claim_preserves_durable_resume_eligibility(gui):
    app,c,fake,registry,_=gui;r=app.state.recorder;key,owner=r.claim('test','bd-1');r.transition(key,owner,'Running');r.transition(key,owner,'NeedsHuman')
    with r.transaction() as db:db.execute('UPDATE runs SET worker_pid=NULL WHERE run_id=?',(key,))
    restarted=create_app(registry,pairing_code='fixture-pairing',adapter_factory=fake.adapter,writer=fake.write,launcher=lambda *a,**kw:SimpleNamespace(pid=99999997,poll=lambda:7),context_runner=git_runner)
    with TestClient(restarted,base_url=ORIGIN) as client:
        client.headers['origin']=ORIGIN;login(client);d=client.get('/api/projects/test/issues/bd-1').json()
        result=client.post('/api/runs/'+key+'/resume',json={'expected':d['fingerprint']});assert result.status_code==200
        job_id=result.json()['job_id'];job=client.get('/api/jobs/'+job_id).json()
        assert job['state']=='failed' and job['exit_code']==7 and job['replacement_run_id'] is None
        assert r.get(key)['state']=='NeedsHuman'
    # The failed job and eligibility survive a server restart, with no volatile Popen handle.
    restarted=create_app(registry,pairing_code='fixture-pairing',adapter_factory=fake.adapter,writer=fake.write,context_runner=git_runner)
    with TestClient(restarted,base_url=ORIGIN) as client:
        client.headers['origin']=ORIGIN;login(client)
        assert client.get('/api/jobs/'+job_id).json()['state']=='failed'
        assert client.get('/api/runs/'+key).json()['can_resume']


@pytest.mark.parametrize('finish_new_review',[False,True])
def test_approval_rechecks_review_identity_inside_decision_transaction(gui,monkeypatch,finish_new_review):
    app,c,*_=gui;login(c);r=app.state.recorder;key,approval=review_run(r)
    preview=c.get('/api/approvals/'+approval).json();consume=app.state.auth.consume
    def race(*args):
        consume(*args)
        with r.transaction() as db:
            db.execute("UPDATE runs SET state='Verifying',active=1,owner='new-review-owner' WHERE run_id=?",(key,))
            r._event(db,key,'review_started',{})
        if finish_new_review:
            r.artifact(key,'new-review',b'new evidence','review');r.transition(key,'new-review-owner','Review')
    monkeypatch.setattr(app.state.auth,'consume',race)
    result=c.post('/api/approvals/'+approval+'/approve',json={k:preview[k] for k in ['challenge','fingerprint']})
    assert result.json()['error']['code']=='STALE_APPROVAL'
    assert next(a for a in r.history(key)['approvals'] if a['approval_id']==approval)['status']=='pending'


def test_restart_revokes_unregistered_admission_and_allows_safe_explicit_retry(gui,monkeypatch):
    import os
    app,c,fake,registry,_=gui;r=app.state.recorder;key,owner=r.claim('test','bd-1');r.transition(key,owner,'Running');r.transition(key,owner,'NeedsHuman')
    with r.transaction() as db:db.execute('UPDATE runs SET worker_pid=NULL WHERE run_id=?',(key,))
    r.release(key);path=r.root/'pending-output.txt';path.write_text('')
    with r.transaction() as db:db.execute('INSERT INTO gui_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',('job_interrupted','test','bd-1',key,None,None,'launching',time.time(),None,str(path)))
    r.note(key,'gui_resume_launch_pending',{'job_id':'job_interrupted'})
    restarted=create_app(registry,pairing_code='fixture-pairing',adapter_factory=fake.adapter,writer=fake.write,launcher=lambda *a,**kw:SimpleNamespace(pid=os.getpid(),poll=lambda:None,wait=lambda **kw:0),context_runner=git_runner)
    assert r.get(key)['state']=='NeedsHuman'
    with r.connect() as db:assert db.execute('SELECT state FROM gui_jobs WHERE job_id=?',('job_interrupted',)).fetchone()[0]=='failed'
    with TestClient(restarted,base_url=ORIGIN) as client:
        client.headers['origin']=ORIGIN;login(client);d=client.get('/api/projects/test/issues/bd-1').json()
        reply=client.post('/api/runs/'+key+'/resume',json={'expected':d['fingerprint']});assert reply.status_code==200
        monkeypatch.setenv('CADENDA_GUI_JOB_ID',reply.json()['job_id'])
        replacement,new_owner=r.claim('test','bd-1');r.transition(replacement,new_owner,'Running');r.transition(replacement,new_owner,'Verifying');r.transition(replacement,new_owner,'Review')
        # A delayed child carrying the revoked admission cannot run even after the replacement finishes.
        monkeypatch.setenv('CADENDA_GUI_JOB_ID','job_interrupted')
        from cadenda.errors import CadendaError
        with pytest.raises(CadendaError) as exc:r.claim('test','bd-1',verification=True)
        assert exc.value.code=='GUI_JOB_STALE'
        assert r.get(replacement)['state']=='Review'


def test_duplicate_gui_serve_preserves_pairing_file_and_releases_lock(gui,monkeypatch):
    import stat
    import uvicorn
    from cadenda.server import serve
    from cadenda.errors import CadendaError
    app,c,fake,registry,_=gui;path=registry.settings.state_root/'gui-pairing-code.txt';calls=[]
    def run(*args,**kwargs):
        calls.append(True);inode=path.stat().st_ino
        assert stat.S_IMODE(path.stat().st_mode)==0o600
        with pytest.raises(CadendaError) as exc:serve(registry,8767)
        assert exc.value.code=='GUI_ALREADY_RUNNING' and path.stat().st_ino==inode
    monkeypatch.setattr(uvicorn,'run',run)
    serve(registry);assert not path.exists()
    serve(registry);assert len(calls)==2 and not path.exists()


def test_maintenance_rejects_http_mutations_before_beads_write(gui):
    from cadenda.coordination import access
    app,c,fake,registry,_=gui;login(c)
    with access(registry.settings.state_root,exclusive=True):
        response=c.post('/api/projects/test/issues/bd-1/comments',json={'kind':'comment','text':'must not persist'})
        assert response.status_code==503 and response.json()['error']['code']=='MAINTENANCE_BUSY'
        assert not fake.calls
        assert c.get('/api/projects').status_code==200


def test_staging_requires_a_separate_owner_confirmation_and_rejects_replay(gui,monkeypatch):
    import cadenda.staging as staging
    app,c,*_=gui;calls=[]
    monkeypatch.setattr(staging,'prepare',lambda reg,key:{'plan':{'operation':'local_stage','run_id':key},'fingerprint':'b'*64})
    monkeypatch.setattr(staging,'apply',lambda *args:(calls.append(args) or {'target':'synthetic','source_executed':False}))
    assert c.post('/api/runs/run_test/stage/preview',json={}).status_code==401
    login(c);preview=c.post('/api/runs/run_test/stage/preview',json={}).json()
    body={'challenge':preview['challenge'],'fingerprint':preview['fingerprint']}
    assert c.post('/api/runs/run_test/stage',json={**body,'challenge':'forged'}).status_code==409
    assert not calls
    preview=c.post('/api/runs/run_test/stage/preview',json={}).json();body={'challenge':preview['challenge'],'fingerprint':preview['fingerprint']}
    assert c.post('/api/runs/run_test/stage',json=body).status_code==200
    assert c.post('/api/runs/run_test/stage',json=body).status_code==409 and len(calls)==1


def test_formal_receipt_is_committed_before_staging_can_read_requirements(gui,monkeypatch):
    from contextlib import contextmanager
    import threading
    from cadenda.process import beads_guard
    app,c,fake,reg,_=gui;login(c)
    body={'topic':'race','text':'Synthetic receipt race','expected':issue_fingerprint(Issue.model_validate(fake.rows['bd-1']))}
    preview=c.post('/api/projects/test/issues/bd-1/decisions/preview',json=body).json()
    original=Recorder.transaction;threads=[];observed=[]
    def stage_reader():
        with beads_guard(reg.project('test').root):observed.extend(receipts(Recorder(reg.settings.state_root,initialize=False),'test'))
    @contextmanager
    def pause_before_receipt(self):
        if not threads and fake.messages['bd-1']:
            thread=threading.Thread(target=stage_reader);threads.append(thread);thread.start();time.sleep(.08)
            assert not observed
        with original(self) as db:yield db
    monkeypatch.setattr(Recorder,'transaction',pause_before_receipt)
    response=c.post('/api/projects/test/issues/bd-1/decisions',json={**body,'challenge':preview['challenge']})
    assert response.status_code==200,response.text
    threads[0].join(3);assert len(observed)==1 and observed[0].topic=='race'


def test_local_open_ticket_is_single_use_and_preserves_origin_csrf(gui):
    import hashlib
    from cadenda.operations import atomic
    app,c,fake,reg,_=gui;root=reg.settings.state_root
    code='synthetic-local-open-ticket'
    atomic(root/'gui-open-ticket.json',{'sha256':hashlib.sha256(code.encode()).hexdigest(),'expires':time.time()+60,'instance':app.state.instance})
    (root/'gui-open.html').write_text('synthetic launcher')
    assert c.post('/api/session/login',json={'code':code},headers={'origin':'https://outside.invalid'}).status_code==403
    assert (root/'gui-open-ticket.json').exists()
    result=c.post('/api/session/login',json={'code':code});assert result.status_code==200
    assert not (root/'gui-open-ticket.json').exists() and not (root/'gui-open.html').exists()
    assert c.post('/api/projects/test/issues',json={}).status_code==403
    assert c.post('/api/session/login',json={'code':code}).status_code==401
    c.headers['x-cadenda-csrf']=result.json()['csrf'];assert c.get('/api/projects').status_code==200


def test_local_open_rejects_expired_or_public_ticket(gui):
    import hashlib
    from cadenda.operations import atomic
    app,c,fake,reg,_=gui;p=reg.settings.state_root/'gui-open-ticket.json';code='synthetic-expired'
    atomic(p,{'sha256':hashlib.sha256(code.encode()).hexdigest(),'expires':time.time()-1,'instance':app.state.instance})
    assert c.post('/api/session/login',json={'code':code}).status_code==401
    atomic(p,{'sha256':hashlib.sha256(code.encode()).hexdigest(),'expires':time.time()+60,'instance':app.state.instance});p.chmod(0o644)
    assert c.post('/api/session/login',json={'code':code}).status_code==401


def test_local_browser_launcher_never_passes_ticket_in_process_arguments(gui,monkeypatch):
    import io,stat,re
    from urllib.parse import urlparse,parse_qs
    from cadenda import local_open
    app,c,fake,reg,_=gui;root=reg.settings.state_root
    (root/'gui-server.lock').write_text(json.dumps({'local_open':1,'port':8765,'instance':app.state.instance}))
    monkeypatch.setattr(local_open.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps({'protocol':1,'workspace':digest(str(root).encode()),'instance':app.state.instance}).encode()))
    calls=[];monkeypatch.setattr(local_open.subprocess,'run',lambda argv,**kw:calls.append(argv))
    import fcntl
    with (root/'gui-server.lock').open('r') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        result=local_open.open_workspace(reg)
    html=(root/'gui-open.html').read_text();target=re.search('url=([^\"]+)',html).group(1);code=parse_qs(urlparse(target).fragment)['connect'][0]
    assert code not in json.dumps(result) and code not in json.dumps(calls)
    assert stat.S_IMODE((root/'gui-open.html').stat().st_mode)==0o600
    assert stat.S_IMODE((root/'gui-open-ticket.json').stat().st_mode)==0o600
    assert local_open.consume(root,code,app.state.instance) and not local_open.consume(root,code,app.state.instance)


def test_invalid_utf8_output_remains_retrievable_and_reviewable(gui):
    import sys
    from cadenda.worker import execute
    app,c,fake,reg,_=gui;login(c);r=app.state.recorder
    key,owner=r.claim('test','bd-1');r.transition(key,owner,'Running')
    execute(r,key,owner,'check-invalid',[sys.executable,'-c','import sys;sys.stdout.buffer.write(bytes([255])*1_048_576)'],reg.project('test').root)
    r.transition(key,owner,'Verifying');r.transition(key,owner,'Review')
    artifact=c.get('/api/runs/'+key+'/artifacts/check-invalid-stdout');assert artifact.status_code==200
    assert 'TRUNCATED' in artifact.json()['text']
    approval=r.history(key)['approvals'][-1]['approval_id']
    assert c.get('/api/approvals/'+approval).status_code==200
    entry=next(a for a in r.history(key)['artifacts'] if a['artifact_id']=='check-invalid-stdout')
    assert Path(entry['path']).stat().st_size<1_049_000


def test_projects_includes_active_slot_even_when_newer_diagnostics_exist(gui):
    app,c,fake,reg,_=gui;login(c);r=app.state.recorder
    older,owner=r.claim('test','bd-1');r.transition(older,owner,'Running');r.transition(older,owner,'NeedsHuman')
    with r.transaction() as db:
        row=dict(db.execute('SELECT * FROM runs WHERE run_id=?',(older,)).fetchone());row.update({'run_id':'run_new_diagnostic','active':0,'state':'Review','created_at':time.time()+1})
        keys=list(row);db.execute('INSERT INTO runs('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',[row[k] for k in keys])
    rows=c.get('/api/projects').json()['runs'];assert rows[0]['run_id']=='run_new_diagnostic'
    assert next(row for row in rows if row['active'])['run_id']==older


def test_local_open_rejects_stale_server_lock_even_with_forged_health(gui,monkeypatch):
    from cadenda import local_open
    from cadenda.errors import CadendaError
    app,c,fake,reg,_=gui;root=reg.settings.state_root
    (root/'gui-server.lock').write_text(json.dumps({'local_open':1,'port':8765,'instance':app.state.instance}))
    monkeypatch.setattr(local_open.urllib.request,'urlopen',lambda *a,**k:pytest.fail('Must reject stale lock before contacting the port'))
    with pytest.raises(CadendaError) as exc:local_open.open_workspace(reg,launch=False)
    assert exc.value.code=='GUI_NOT_RUNNING'
    assert not (root/'gui-open-ticket.json').exists()


def test_local_open_ticket_cannot_cross_server_restart(gui):
    import hashlib
    from cadenda.operations import atomic
    app,c,fake,reg,_=gui;code='synthetic-old-instance'
    atomic(reg.settings.state_root/'gui-open-ticket.json',{'sha256':hashlib.sha256(code.encode()).hexdigest(),'expires':time.time()+60,'instance':'old-server-instance'})
    assert c.post('/api/session/login',json={'code':code}).status_code==401


def test_approval_inbox_shows_only_latest_request_for_review_ready_run(gui):
    app,c,fake,reg,_=gui;login(c);r=app.state.recorder;key,old=review_run(r)
    latest=r.request_approval(key,{'synthetic_latest':True})
    items=c.get('/api/approvals').json()['approvals']
    assert [a['approval_id'] for a in items]==[latest]
    with r.transaction() as db:db.execute("UPDATE runs SET state='Verifying',active=1 WHERE run_id=?",(key,))
    assert not c.get('/api/approvals').json()['approvals']


def test_issue_list_accepts_actual_beads_full_edge_shape(gui):
    app,c,fake,reg,_=gui;login(c)
    fake.rows['bd-1']['dependencies']=[{'issue_id':'bd-1','depends_on_id':'bd-0','type':'blocks','metadata':'{}'}]
    fake.rows['bd-1']['dependents']=[{'issue_id':'bd-2','depends_on_id':'bd-1','type':'blocks'}]
    response=c.get('/api/projects/test/issues');assert response.status_code==200
    issue=response.json()['issues'][0]
    assert issue['dependencies']==[{'id':'bd-0','dependency_type':'blocks'}]
    assert issue['dependents']==[{'id':'bd-2','dependency_type':'blocks'}]


def test_issue_list_schema_error_is_structured_json(gui):
    app,c,fake,reg,_=gui;login(c)
    fake.rows['bd-1']['dependencies']=[{'issue_id':'bd-1','type':'blocks'}]
    response=c.get('/api/projects/test/issues')
    assert response.status_code==409
    assert response.headers['content-type'].startswith('application/json')
    assert response.json()['error']['code']=='SCHEMA_MISMATCH'
