"""Loopback owner UI. Fixed operations only; no HTTP command/path passthrough."""
import asyncio
from contextlib import asynccontextmanager,closing
from datetime import datetime,timezone
import json
import fcntl
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI,Request,Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,FileResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .beads_adapter import BeadsAdapter
from .config import StrictModel,ID,DecisionReceipt
from .context_builder import ContextBuilder
from .errors import CadendaError
from .gui_auth import OwnerAuth
from .human_store import initialize,issue_fingerprint,receipts
from .process import capture
from .recorder import Recorder
from .security import canonical,digest,identifier,read_bounded,redact
from .snapshots import SnapshotStore


class Login(StrictModel):code:str=Field(min_length=1,max_length=128)
class IssueInput(StrictModel):
    title:str=Field(min_length=1,max_length=200)
    description:str=Field(max_length=16000)
    acceptance_criteria:str=Field(max_length=8000)
class IssueEdit(IssueInput):expected:str=Field(pattern='^[a-f0-9]{64}$')
class CommentInput(StrictModel):
    kind:Literal['comment','decision_proposed','requirement_changed']
    text:str=Field(min_length=1,max_length=8000)
class DecisionInput(StrictModel):
    topic:ID
    text:str=Field(min_length=1,max_length=8000)
    expected:str=Field(pattern='^[a-f0-9]{64}$')
class DecisionConfirm(DecisionInput):challenge:str=Field(max_length=100)
class ApprovalInput(StrictModel):
    challenge:str=Field(max_length=100)
    fingerprint:str=Field(pattern='^[a-f0-9]{64}$')
class StartInput(StrictModel):
    mode:Literal['check','pipeline','local']='check'
    task:Literal['classify','extract','names','clamp']='classify'
    text:str=Field(default='',max_length=4000)
    expected:str=Field(pattern='^[a-f0-9]{64}$')


def safe_input(value):
    _,count=redact(value)
    if count:raise CadendaError('DATA_DENIED','Remove credential/control patterns before submitting')



class RequestLimit:
    def __init__(self,app,origin,auth,body_timeout=10):
        self.app=app;self.origin=origin;self.auth=auth;self.body_timeout=body_timeout;self.slots=asyncio.Semaphore(16)
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] in {'GET','HEAD'}:
            return await self.app(scope,receive,send)
        req=Request(scope)
        async def deny(code,status):
            await JSONResponse({'error':{'code':code}},status_code=status)(scope,receive,send)
        if req.headers.get('host')!=self.origin.removeprefix('http://') or req.client.host not in {'127.0.0.1','::1','testclient'}:return await deny('HOST_DENIED',403)
        if req.headers.get('origin')!=self.origin:return await deny('ORIGIN_DENIED',403)
        if req.headers.get('content-type','').split(';')[0]!='application/json':return await deny('JSON_REQUIRED',415)
        if req.url.path.startswith('/api/') and req.url.path!='/api/session/login':
            try:session=self.auth.session(req.cookies.get('cadenda_session'))
            except CadendaError:return await deny('UNAUTHENTICATED',401)
            if not secrets.compare_digest(req.headers.get('x-cadenda-csrf',''),session.csrf):return await deny('CSRF_DENIED',403)
        body=bytearray()
        try:
            async with asyncio.timeout(self.body_timeout):
                async with self.slots:
                    while True:
                        message=await receive()
                        if message['type']=='http.disconnect':return
                        chunk=message.get('body',b'')
                        if len(body)+len(chunk)>65536:return await deny('BODY_LIMIT',413)
                        body.extend(chunk)
                        if not message.get('more_body',False):break
        except TimeoutError:return await deny('BODY_TIMEOUT',408)
        delivered=False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered=True;return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)


def create_app(registry,*,pairing_code,port=8765,adapter_factory=BeadsAdapter,writer=capture,launcher=None,context_runner=capture):
    recorder=Recorder(registry.settings.state_root);initialize(recorder)
    auth=OwnerAuth(pairing_code);origin=f'http://127.0.0.1:{port}';lock=threading.RLock();jobs={}
    project_ids={p.project_id for p in registry.settings.projects}
    recorder.recover(project_ids=project_ids)
    config_path=getattr(registry,'config_path',None)

    def audit(actor,action,target,payload):
        safe,_=redact(payload)
        with recorder.transaction() as db:db.execute('INSERT INTO owner_audit(occurred_at,actor_ref,action,target,payload) VALUES(?,?,?,?,?)',(time.time(),actor,action,target,json.dumps(safe)))

    def write(project,*args):
        code,out,err=writer(['bd','--sandbox','--directory',str(project.root),'--json',*args],project.root)
        try:payload=json.loads(out)
        except ValueError:raise CadendaError('BEADS_WRITE_FAILED','Beads did not acknowledge the operation')
        if code or err.strip() or (isinstance(payload,dict) and (payload.get('error') or payload.get('unsupported'))):
            raise CadendaError('BEADS_WRITE_FAILED','Beads rejected the operation; inspect local state')
        return payload

    def get_issue(project_id,issue_id):
        identifier(issue_id);project=registry.project(project_id);issue,_=adapter_factory(project).issue(issue_id)
        return project,issue

    def expected_issue(project_id,issue_id,expected):
        project,issue=get_issue(project_id,issue_id)
        if issue_fingerprint(issue)!=expected:raise CadendaError('STALE_REQUIREMENTS','Reload the Issue before making changes')
        return project,issue

    def run_row(run_id):
        row=recorder.get(run_id);registry.project(row['project_id']);return row

    def requirement_binding(project_id,issue_id,*,strict=True):
        project,issue=get_issue(project_id,issue_id)
        comments,_=adapter_factory(project).comments(issue_id)
        latest={}
        for r in sorted([*project.decisions,*receipts(recorder,project_id)],key=lambda r:r.approved_at):
            if issue_id in r.applies_to:latest[r.topic]=r
        verified=[]
        for r in latest.values():
            try:
                _,source_issue=get_issue(project_id,r.issue_id)
                source_comments,_=adapter_factory(project).comments(r.issue_id)
                c=next((c for c in source_comments if str(c.id)==r.comment_id),None)
            except CadendaError as exc:
                if strict:raise CadendaError('DECISION_UNVERIFIED','Current approved requirements cannot be retrieved') from exc
                verified.append({'receipt':r.model_dump(mode='json'),'source_issue_sha256':None,'comment':None,'verified':False,'source_error':exc.code})
                continue
            valid=bool(c and digest(c.text.encode())==r.comment_sha256 and (not r.issue_sha256 or issue_fingerprint(source_issue)==r.issue_sha256))
            if strict and not valid:raise CadendaError('DECISION_UNVERIFIED','Current approved requirements cannot be verified')
            verified.append({'receipt':r.model_dump(mode='json'),'source_issue_sha256':issue_fingerprint(source_issue),'comment':c.model_dump() if c else None,'verified':valid})
        return {'issue':issue.model_dump(),'issue_sha256':issue_fingerprint(issue),'comments_sha256':digest(canonical([c.model_dump() for c in comments])),'receipts':verified}

    def approval_target(approval_id):
        identifier(approval_id)
        with closing(recorder.connect()) as db:
            a=db.execute('SELECT * FROM approvals WHERE approval_id=?',(approval_id,)).fetchone()
        if not a or a['status']!='pending':raise CadendaError('STALE_APPROVAL','Approval is no longer pending')
        row=run_row(a['run_id']);history=recorder.history(row['run_id'])
        events=[e for e in history['events'] if e['event_type']=='approval_requested']
        states=[e for e in history['events'] if e['event_type']=='state_changed' and e['payload']['to']=='Review']
        if row['state']!='Review' or not events or events[-1]['payload']['approval_id']!=approval_id or not states:
            raise CadendaError('STALE_APPROVAL','Only the latest review request may be decided')
        binding=states[-1]['payload'].get('review_binding')
        if not binding or digest(canonical(binding))!=a['action_hash'] or binding['run_id']!=row['run_id']:
            raise CadendaError('LEGACY_APPROVAL','This run needs a fresh review with artifact binding')
        if time.time()-datetime.fromisoformat(events[-1]['occurred_at']).timestamp()>86400:
            raise CadendaError('EXPIRED_APPROVAL','This review request is older than 24 hours')
        artifacts={x['artifact_id']:x for x in history['artifacts']}
        for target in binding['artifacts']:
            artifact=artifacts.get(target['artifact_id'])
            if not artifact or artifact['sha256']!=target['sha256']:raise CadendaError('ARTIFACT_CORRUPT','Bound artifact metadata changed')
            body=read_bounded(recorder.root,str(Path(artifact['path']).relative_to(recorder.root)),2_000_000)
            if digest(body)!=target['sha256']:raise CadendaError('ARTIFACT_CORRUPT','Bound artifact changed')
        if row['commit_id']:
            from .worker import Worker
            project=registry.project(row['project_id']);wt=project.allowed_worktree(Path(row['worktree']))
            if Worker(registry).git(wt,'rev-parse','HEAD')!=row['commit_id'] or Worker(registry).git(wt,'status','--porcelain'):
                raise CadendaError('STALE_EVIDENCE','Worktree changed since review')
        _,issue=get_issue(row['project_id'],row['issue_id'])
        target={'approval_id':approval_id,'run_id':row['run_id'],'project_id':row['project_id'],'issue_id':row['issue_id'],
            'action_hash':a['action_hash'],'binding':binding,'requirements':requirement_binding(row['project_id'],row['issue_id'])}
        return target,digest(canonical(target))

    def restore_failed_resume(db,run_id,job_id,reason):
        old=db.execute('SELECT * FROM runs WHERE run_id=?',(run_id,)).fetchone()
        latest=db.execute('SELECT run_id FROM runs WHERE project_id=? AND issue_id=? ORDER BY created_at DESC LIMIT 1',(old['project_id'],old['issue_id'])).fetchone()
        if latest['run_id']!=run_id:return
        if old['state']=='Abandoned' and not db.execute('SELECT 1 FROM runs WHERE active=1').fetchone():
            db.execute("UPDATE runs SET state='NeedsHuman',active=1,owner=?,updated_at=? WHERE run_id=?",(uuid4().hex,time.time(),run_id))
            recorder._event(db,run_id,'state_changed',{'from':'Abandoned','to':'NeedsHuman','reason':reason})
        recorder._event(db,run_id,'gui_resume_launch_failed',{'job_id':job_id,'retryable':True,'reason':reason})

    def reconcile_jobs(*,revoke_pending=False):
        with lock:
            if revoke_pending:
                # Revoke unregistered admission atomically; do not assume a child is absent.
                # Recorder.claim checks this same row before admitting any work.
                with recorder.transaction() as db:
                    pending=db.execute("SELECT * FROM gui_jobs WHERE state='launching' AND worker_pid IS NULL AND replacement_run_id IS NULL").fetchall()
                    for row in pending:
                        if row['project_id'] not in project_ids:continue
                        db.execute("UPDATE gui_jobs SET state='failed' WHERE job_id=?",(row['job_id'],))
                        if row['prior_run_id']:restore_failed_resume(db,row['prior_run_id'],row['job_id'],'ADMISSION_REVOKED')
            with closing(recorder.connect()) as db:rows=db.execute("SELECT * FROM gui_jobs WHERE state IN ('launching','launched','claimed')").fetchall()
            for row in rows:
                if row['project_id'] not in project_ids:continue
                handle=jobs.get(row['job_id'],{}).get('process')
                code=handle.poll() if handle else None
                ended=(code is not None) if handle else bool(row['worker_pid'] and not Recorder.alive(row['worker_pid']))
                if not ended:continue
                with recorder.transaction() as db:
                    current=db.execute('SELECT * FROM gui_jobs WHERE job_id=?',(row['job_id'],)).fetchone()
                    if current['state'] not in {'launching','launched','claimed'}:continue
                    replacement=current['replacement_run_id']
                    db.execute('UPDATE gui_jobs SET state=?,exit_code=? WHERE job_id=?',('completed' if replacement else 'failed',code,row['job_id']))
                    if current['prior_run_id'] and not replacement:restore_failed_resume(db,current['prior_run_id'],row['job_id'],'EXIT_BEFORE_CLAIM')

    def start_job(project_id,issue_id,data,actor,*,resume_of=None):
        if not config_path:raise CadendaError('CONFIG_REQUIRED','Use an owner configuration file to start runs')
        expected_issue(project_id,issue_id,data.expected);safe_input(data.text)
        if any(j['process'].poll() is None for j in jobs.values()):raise CadendaError('RUN_CONFLICT','A GUI job is already running')
        with closing(recorder.connect()) as db:
            if db.execute('SELECT 1 FROM runs WHERE active=1 AND run_id!=?',(resume_of or '',)).fetchone():raise CadendaError('RUN_CONFLICT','A run is already active')
        project=registry.project(project_id)
        if data.mode=='pipeline' and not project.cloud_enabled:raise CadendaError('CLOUD_DISABLED','Cloud is disabled for this project')
        if data.mode=='local' and not project.fm_local_enabled:raise CadendaError('LOCAL_DISABLED','Local fm is disabled for this project')
        if data.mode=='local' and len(data.text.encode())>4000:raise CadendaError('FM_INPUT_LIMIT','Local input exceeds 4000 UTF-8 bytes')
        args=['check',project_id,issue_id] if data.mode=='check' else ['pipeline',project_id,issue_id,'--max-fixes','2'] if data.mode=='pipeline' else ['local',project_id,issue_id,data.task,'--',data.text]
        key='job_'+uuid4().hex;dest=recorder.root/'gui-jobs';dest.mkdir(exist_ok=True,mode=0o700)
        if dest.resolve()!=dest:raise CadendaError('PATH_DENIED','Job directory changed')
        path=dest/(key+'.txt');fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:
            with recorder.transaction() as db:db.execute('INSERT INTO gui_jobs VALUES(?,?,?,?,NULL,NULL,?, ?,NULL,?)',(key,project_id,issue_id,resume_of,'launching',time.time(),str(path)))
            if resume_of:
                previous=run_row(resume_of)
                recorder.note(resume_of,'gui_resume_launch_pending',{'job_id':key})
                if previous['state']=='NeedsHuman':recorder.release(resume_of)
            try:
                proc=(launcher or subprocess.Popen)([sys.executable,'-I','-m','cadenda.cli','--config',str(config_path),'run',*args],cwd=registry.project(project_id).root,stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True,env=os.environ|{'CADENDA_GUI_JOB_ID':key})
            except OSError as exc:
                with recorder.transaction() as db:
                    db.execute("UPDATE gui_jobs SET state='failed' WHERE job_id=?",(key,))
                    if resume_of:restore_failed_resume(db,resume_of,key,'LAUNCH_FAILED')
                raise CadendaError('LAUNCH_FAILED','Worker could not start; the original run remains recoverable') from exc
        with recorder.transaction() as db:
            registered=db.execute("UPDATE gui_jobs SET worker_pid=?,state=CASE WHEN state IN ('failed','completed') THEN state WHEN replacement_run_id IS NULL THEN 'launched' ELSE 'claimed' END WHERE job_id=? AND (state!='failed' OR replacement_run_id IS NOT NULL) AND (worker_pid IS NULL OR worker_pid=?)",(proc.pid,key,proc.pid))
            if registered.rowcount!=1:raise CadendaError('GUI_JOB_STALE','This launch admission was revoked before registration')
        if resume_of:recorder.note(resume_of,'gui_resume_launched',{'job_id':key,'worker_pid':proc.pid})
        jobs[key]={'process':proc,'project_id':project_id,'issue_id':issue_id,'path':path}
        audit(actor,'run_start',key,{'project_id':project_id,'issue_id':issue_id,'mode':data.mode})
        return {'job_id':key}

    @asynccontextmanager
    async def lifespan(app):
        async def monitor():
            while True:
                await asyncio.to_thread(reconcile_jobs)
                await asyncio.sleep(.5)
        monitor_task=asyncio.create_task(monitor())
        try:yield
        finally:
            monitor_task.cancel()
            try:await monitor_task
            except asyncio.CancelledError:pass
        # Ask owned workers to clean their own subprocess groups before stopping the UI.
        for job in jobs.values():
            if job['process'].poll() is None:
                with closing(recorder.connect()) as db:
                    rows=db.execute('SELECT run_id FROM runs WHERE worker_pid=? AND active=1',(job['process'].pid,)).fetchall()
                for row in rows:recorder.cancel(row['run_id'])
        for job in jobs.values():
            if job['process'].poll() is None:
                try:await asyncio.to_thread(job['process'].wait,timeout=8)
                except subprocess.TimeoutExpired:
                    job['process'].terminate()
                    try:await asyncio.to_thread(job['process'].wait,timeout=3)
                    except subprocess.TimeoutExpired:
                        job['process'].kill();await asyncio.to_thread(job['process'].wait)

    app=FastAPI(title='Cadenda',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.recorder=recorder;app.state.auth=auth;app.state.jobs=jobs

    @app.middleware('http')
    async def guard(request,call_next):
        def deny(code,status):return JSONResponse({'error':{'code':code}},status_code=status)
        if request.headers.get('host')!=f'127.0.0.1:{port}' or request.client.host not in {'127.0.0.1','::1','testclient'}:return deny('HOST_DENIED',403)
        if request.headers.get('origin') not in {None,origin}:return deny('ORIGIN_DENIED',403)
        if request.method not in {'GET','HEAD'}:
            if request.headers.get('origin')!=origin:return deny('ORIGIN_REQUIRED',403)
            if request.headers.get('content-type','').split(';')[0]!='application/json':return deny('JSON_REQUIRED',415)
        if request.url.path.startswith('/api/') and request.url.path!='/api/session/login':
            try:session=auth.session(request.cookies.get('cadenda_session'))
            except CadendaError:return deny('UNAUTHENTICATED',401)
            request.state.owner=session
            if request.method not in {'GET','HEAD'} and not secrets.compare_digest(request.headers.get('x-cadenda-csrf',''),session.csrf):return deny('CSRF_DENIED',403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(CadendaError)
    async def known_error(request,exc):
        return JSONResponse({'error':{'code':exc.code,'message':redact(str(exc))[0]}},status_code=401 if exc.code=='UNAUTHENTICATED' else 409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):return JSONResponse({'error':{'code':'INVALID_REQUEST','message':'Check the submitted fields'}},status_code=422)

    @app.exception_handler(OSError)
    async def io_error(request,exc):return JSONResponse({'error':{'code':'IO_ERROR','message':'Local operation failed'}},status_code=503)

    @app.post('/api/session/login')
    def login(data:Login):
        with lock:token,session=auth.login(data.code)
        response=JSONResponse({'actor':session.actor,'csrf':session.csrf,'expires_at':session.expires})
        response.set_cookie('cadenda_session',token,httponly=True,samesite='strict',max_age=8*3600,path='/')
        return response

    @app.get('/api/session')
    def session(request:Request):return vars(request.state.owner)

    @app.post('/api/session/logout')
    def logout(request:Request):
        auth.logout(request.cookies.get('cadenda_session'))
        response=JSONResponse({'logged_out':True});response.delete_cookie('cadenda_session');return response

    @app.get('/api/projects')
    def projects():
        with closing(recorder.connect()) as db:
            db.execute('BEGIN')
            cursor=db.execute('SELECT COALESCE(MAX(rowid),0) FROM run_events').fetchone()[0]
            rows=[dict(r) for r in db.execute('SELECT run_id,project_id,issue_id,state,provider,updated_at FROM runs ORDER BY created_at DESC LIMIT 500')]
            db.commit()
        return {'cursor':cursor,'projects':[{'project_id':p.project_id,'cloud_enabled':p.cloud_enabled,'fm_local_enabled':p.fm_local_enabled} for p in registry.settings.projects],'runs':[r for r in rows if r['project_id'] in project_ids]}

    @app.get('/api/providers/health')
    def health():return {'providers':[{'name':'Executor','status':'configured checks'},{'name':'Codex / Antigravity','status':'project opt-in only'},{'name':'fm system','status':'project opt-in only'},{'name':'PCC','status':'disabled / separate consent required'}],'probe':'configuration only; no inference or authentication probe'}

    @app.get('/api/projects/{project_id}/issues')
    def issues(project_id:str):
        project=registry.project(project_id);raw=adapter_factory(project)._json('list','--all','--brief','--limit=200')
        if not isinstance(raw,list):raise CadendaError('SCHEMA_MISMATCH','Expected Beads issue list')
        from .beads_adapter import Issue
        return {'issues':redact([Issue.model_validate(row).model_dump() for row in raw])[0],'limit':200}

    @app.post('/api/projects/{project_id}/issues')
    def create_issue(project_id:str,data:IssueInput,request:Request):
        safe_input(data.model_dump());project=registry.project(project_id)
        with lock:
            result=write(project,'create','--title='+data.title,'--description='+data.description,'--acceptance='+data.acceptance_criteria)
            if not isinstance(result,dict) or not isinstance(result.get('id'),str):raise CadendaError('BEADS_WRITE_FAILED','Issue acknowledgement missing')
            _,saved=get_issue(project_id,result['id'])
            if any(getattr(saved,k)!=getattr(data,k) for k in ('title','description','acceptance_criteria')):raise CadendaError('BEADS_WRITE_FAILED','Created Issue does not match the submitted fields')
            audit(request.state.owner.actor,'issue_create',project_id+'/'+result['id'],data.model_dump())
        return {'issue_id':result['id']}

    @app.get('/api/projects/{project_id}/issues/{issue_id}')
    def issue_detail(project_id:str,issue_id:str):
        project,issue=get_issue(project_id,issue_id);comments,_=adapter_factory(project).comments(issue_id)
        with closing(recorder.connect()) as db:runs=[dict(r) for r in db.execute('SELECT run_id,state,provider,created_at,updated_at FROM runs WHERE project_id=? AND issue_id=? ORDER BY created_at DESC LIMIT 100',(project_id,issue_id))]
        with closing(recorder.connect()) as db:job_rows=[dict(row) for row in db.execute('SELECT job_id,state,exit_code,created_at,replacement_run_id FROM gui_jobs WHERE project_id=? AND issue_id=? ORDER BY created_at DESC LIMIT 30',(project_id,issue_id))]
        return redact({'issue':issue.model_dump(),'fingerprint':issue_fingerprint(issue),'comments':[c.model_dump() for c in comments],'runs':runs,'jobs':job_rows})[0]

    @app.put('/api/projects/{project_id}/issues/{issue_id}')
    def edit_issue(project_id:str,issue_id:str,data:IssueEdit,request:Request):
        safe_input(data.model_dump())
        with lock:
            project,issue=expected_issue(project_id,issue_id,data.expected)
            write(project,'update',issue_id,'--title='+data.title,'--description='+data.description,'--acceptance='+data.acceptance_criteria)
            _,saved=get_issue(project_id,issue_id)
            if any(getattr(saved,k)!=getattr(data,k) for k in ('title','description','acceptance_criteria')):raise CadendaError('BEADS_WRITE_FAILED','Saved Issue does not match the submitted fields')
            audit(request.state.owner.actor,'issue_update',project_id+'/'+issue_id,data.model_dump())
        return {'updated':issue_id}

    @app.post('/api/projects/{project_id}/issues/{issue_id}/comments')
    def comment(project_id:str,issue_id:str,data:CommentInput,request:Request):
        safe_input(data.text);project,issue=get_issue(project_id,issue_id)
        with lock:
            text=f'[{data.kind}] '+data.text
            result=write(project,'comments','add','--',issue_id,text)
            if not isinstance(result,dict) or result.get('issue_id')!=issue_id or result.get('text')!=text:raise CadendaError('BEADS_WRITE_FAILED','Comment not acknowledged')
            audit(request.state.owner.actor,data.kind,project_id+'/'+issue_id,{'comment_id':result['id']})
        return {'comment_id':result['id']}

    def decision_payload(project_id,issue_id,data):
        safe_input(data.text);project,issue=expected_issue(project_id,issue_id,data.expected)
        value={'project_id':project_id,'issue_id':issue_id,'topic':data.topic,'text':data.text,'expected':data.expected,'current_requirements':requirement_binding(project_id,issue_id,strict=False)}
        return project,issue,value,digest(canonical(value))

    @app.post('/api/projects/{project_id}/issues/{issue_id}/decisions/preview')
    def decision_preview(project_id:str,issue_id:str,data:DecisionInput,request:Request):
        _,_,value,sha=decision_payload(project_id,issue_id,data)
        return {'decision':redact(value)[0],**auth.challenge(request.state.owner,'decision:'+project_id+'/'+issue_id,sha)}

    @app.post('/api/projects/{project_id}/issues/{issue_id}/decisions')
    def decision(project_id:str,issue_id:str,data:DecisionConfirm,request:Request):
        with lock:
            project,issue,value,sha=decision_payload(project_id,issue_id,data)
            auth.consume(request.state.owner,data.challenge,'decision:'+project_id+'/'+issue_id,sha)
            now=datetime.now(timezone.utc);text=f'[decision_approved] {data.topic}\n{data.text}\n[owner-confirmation:{sha}]'
            result=write(project,'comments','add','--',issue_id,text)
            if not isinstance(result,dict) or result.get('issue_id')!=issue_id or result.get('text')!=text:raise CadendaError('BEADS_WRITE_FAILED','Decision comment not acknowledged')
            expected_issue(project_id,issue_id,data.expected)
            receipt=DecisionReceipt(topic=data.topic,issue_id=issue_id,comment_id=str(result['id']),comment_sha256=digest(text.encode()),applies_to=[issue_id],approved_at=now,approval_ref=request.state.owner.actor,issue_sha256=data.expected)
            with recorder.transaction() as db:db.execute('INSERT INTO human_receipts VALUES(?,?,?)',(project_id,receipt.model_dump_json(),request.state.owner.actor))
            audit(request.state.owner.actor,'decision_confirmed',project_id+'/'+issue_id,receipt.model_dump(mode='json'))
        return {'receipt':receipt.model_dump(mode='json')}

    @app.get('/api/projects/{project_id}/issues/{issue_id}/context')
    def context(project_id:str,issue_id:str,mode:Literal['standard','debug','review','full']='standard'):
        get_issue(project_id,issue_id)
        ctx=ContextBuilder(registry,adapter_factory=adapter_factory,runner=context_runner).build(project_id,issue_id,mode=mode,budget_tokens=150000)
        return {'snapshot':ctx.model_dump(mode='json'),'markdown':ctx.markdown()}

    @app.get('/api/contexts/{snapshot_id}/sources/{source_id}')
    def source(snapshot_id:str,source_id:str,page:int=Query(default=0,ge=0)):
        store=SnapshotStore(recorder.root);ctx=store.load(snapshot_id);registry.project(ctx.project_id)
        return store.page(snapshot_id,source_id,page)

    @app.post('/api/projects/{project_id}/issues/{issue_id}/runs')
    def start(project_id:str,issue_id:str,data:StartInput,request:Request):
        with lock:return start_job(project_id,issue_id,data,request.state.owner.actor)

    @app.get('/api/jobs/{job_id}')
    def job(job_id:str):
        identifier(job_id);reconcile_jobs()
        with closing(recorder.connect()) as db:row=db.execute('SELECT * FROM gui_jobs WHERE job_id=?',(job_id,)).fetchone()
        if not row:raise CadendaError('NOT_FOUND','Job not found')
        registry.project(row['project_id'])
        text=read_bounded(recorder.root,str(Path(row['output_path']).relative_to(recorder.root)),2_000_000).decode(errors='replace')
        return {'job_id':job_id,'state':row['state'],'replacement_run_id':row['replacement_run_id'],'exit_code':row['exit_code'],'output':redact(text)[0]}

    def retryable_resume(row):
        if row['state']!='Abandoned':return False
        history=recorder.history(row['run_id'])
        intents=[e for e in history['events'] if e['event_type'] in {'gui_resume_launch_failed','gui_resume_launched'}]
        return bool(intents and intents[-1]['event_type']=='gui_resume_launch_failed' and intents[-1]['payload'].get('retryable'))

    @app.get('/api/runs/{run_id}')
    def run(run_id:str):
        row=run_row(run_id);result=recorder.history(run_id)
        result['can_resume']=row['state'] in {'Interrupted','NeedsHuman'} or retryable_resume(row)
        return redact(result)[0]

    @app.get('/api/runs/{run_id}/artifacts/{artifact_id}')
    def artifact(run_id:str,artifact_id:str):
        run_row(run_id);identifier(artifact_id);h=recorder.history(run_id)
        a=next((a for a in h['artifacts'] if a['artifact_id']==artifact_id),None)
        if not a:raise CadendaError('NOT_FOUND','Artifact not found')
        body=read_bounded(recorder.root,str(Path(a['path']).relative_to(recorder.root)),2_000_000)
        if digest(body)!=a['sha256']:raise CadendaError('ARTIFACT_CORRUPT','Artifact checksum changed')
        return {'artifact_id':artifact_id,'sha256':a['sha256'],'text':redact(body.decode(errors='replace'))[0]}

    @app.post('/api/runs/{run_id}/cancel')
    def cancel(run_id:str,request:Request):
        row=run_row(run_id)
        if row['state'] not in {'Claimed','Running','Verifying'}:raise CadendaError('STATE_CONFLICT','Run is not executing')
        recorder.cancel(run_id);audit(request.state.owner.actor,'run_cancel',run_id,{})
        return {'cancel_requested':run_id}

    @app.post('/api/runs/{run_id}/resume')
    def resume(run_id:str,data:StartInput,request:Request):
        with lock:
            row=run_row(run_id)
            if row['state'] not in {'Interrupted','NeedsHuman'} and not retryable_resume(row):raise CadendaError('STATE_CONFLICT','Only stopped runs may be explicitly restarted')
            if Recorder.alive(row['worker_pid']) or Recorder.group_alive(row['pgid']):raise CadendaError('RECOVERY_BLOCKED','Original processes must stop first')
            with closing(recorder.connect()) as db:latest=db.execute('SELECT run_id FROM runs WHERE project_id=? AND issue_id=? ORDER BY created_at DESC LIMIT 1',(row['project_id'],row['issue_id'])).fetchone()
            if latest['run_id']!=run_id:raise CadendaError('STALE_RUN','A newer run already exists for this Issue')
            if row['state']=='Interrupted':recorder.recover(project_ids=project_ids)
            return start_job(row['project_id'],row['issue_id'],data,request.state.owner.actor,resume_of=run_id)

    @app.post('/api/recovery')
    def recovery(request:Request):
        with lock:
            reconcile_jobs(revoke_pending=True)
            recovered=recorder.recover(project_ids=project_ids)
            audit(request.state.owner.actor,'recover','workspace',{'runs':recovered})
        return {'recovered':recovered,'processes_killed':False}

    @app.get('/api/approvals')
    def approvals():
        with closing(recorder.connect()) as db:
            rows=[dict(r) for r in db.execute('SELECT a.approval_id,a.run_id,a.action_hash,a.status,r.project_id,r.issue_id,r.updated_at FROM approvals a JOIN runs r USING(run_id) WHERE a.status=\'pending\' ORDER BY r.updated_at DESC LIMIT 200')]
        return {'approvals':[r for r in rows if r['project_id'] in project_ids]}

    @app.get('/api/approvals/{approval_id}')
    def approval_preview(approval_id:str,request:Request):
        target,sha=approval_target(approval_id)
        return {'target':redact(target)[0],**auth.challenge(request.state.owner,'approval:'+approval_id,sha)}

    @app.post('/api/approvals/{approval_id}/{verdict}')
    def decide_approval(approval_id:str,verdict:Literal['approve','reject'],data:ApprovalInput,request:Request):
        with lock:
            target,sha=approval_target(approval_id)
            if data.fingerprint!=sha:raise CadendaError('STALE_APPROVAL','The reviewed target changed')
            auth.consume(request.state.owner,data.challenge,'approval:'+approval_id,sha)
            with recorder.transaction() as db:
                current=db.execute('SELECT * FROM runs WHERE run_id=?',(target['run_id'],)).fetchone()
                latest=db.execute("SELECT payload FROM run_events WHERE run_id=? AND event_type='approval_requested' ORDER BY rowid DESC LIMIT 1",(target['run_id'],)).fetchone()
                review=db.execute("SELECT payload FROM run_events WHERE run_id=? AND event_type='state_changed' ORDER BY rowid DESC LIMIT 1",(target['run_id'],)).fetchone()
                binding=json.loads(review['payload']).get('review_binding') if review else None
                if current['state']!='Review' or current['active'] or not latest or json.loads(latest['payload']).get('approval_id')!=approval_id or not binding or digest(canonical(binding))!=target['action_hash'] or current['commit_id']!=binding['commit']:
                    raise CadendaError('STALE_APPROVAL','The run entered a different review before confirmation')
                result=db.execute("UPDATE approvals SET status=?,actor_ref=?,decided_at=? WHERE approval_id=? AND status='pending'",('approved' if verdict=='approve' else 'rejected',request.state.owner.actor,time.time(),approval_id))
                if result.rowcount!=1:raise CadendaError('STALE_APPROVAL','Already decided')
                recorder._event(db,target['run_id'],'human_review_decided',{'approval_id':approval_id,'verdict':verdict,'actor_ref':request.state.owner.actor,'fingerprint':sha})
            audit(request.state.owner.actor,'review_'+verdict,approval_id,{'fingerprint':sha})
        return {'status':'approved' if verdict=='approve' else 'rejected','execution_authorized':False}

    @app.get('/api/events/stream')
    async def stream(request:Request,after:int=Query(default=0,ge=0,le=9223372036854775807)):
        async def events():
            cursor=after;beats=0
            while request.state.owner.expires>time.time():
                try:auth.session(request.cookies.get('cadenda_session'))
                except CadendaError:return
                with closing(recorder.connect()) as db:rows=db.execute('SELECT rowid,* FROM run_events WHERE rowid>? ORDER BY rowid LIMIT 100',(cursor,)).fetchall()
                for row in rows:
                    cursor=row['rowid'];r=recorder.get(row['run_id'])
                    if r['project_id'] not in project_ids:continue
                    data=redact({'run_id':row['run_id'],'project_id':r['project_id'],'issue_id':r['issue_id'],'event_type':row['event_type'],'payload':json.loads(row['payload'])})[0]
                    if row['event_type']=='artifact_recorded':
                        try:
                            item=artifact(row['run_id'],data['payload']['artifact_id'])
                            data['log']={'artifact_id':item['artifact_id'],'sha256':item['sha256'],'text':item['text'][:16000],'excerpt':len(item['text'])>16000}
                        except (CadendaError,OSError):data['log']={'unavailable':True}
                    yield f'id: {cursor}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n'
                beats+=1
                yield ': heartbeat\n\n'
                if beats%5==0:yield 'event: refresh\ndata: {}\n\n'
                await asyncio.sleep(1)
        # Reconnection sends Last-Event-ID; use it only as a validated row cursor.
        last=request.headers.get('last-event-id')
        if last:
            try:after=max(after,int(last))
            except ValueError:raise CadendaError('INVALID_CURSOR','Invalid event cursor')
            if not 0<=after<=9223372036854775807:raise CadendaError('INVALID_CURSOR','Invalid event cursor')
        return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    ui=Path(__file__).with_name('gui')
    if (ui/'assets').is_dir():app.mount('/assets',StaticFiles(directory=ui/'assets'),name='assets')
    @app.get('/')
    def index():
        if not (ui/'index.html').is_file():raise CadendaError('GUI_NOT_BUILT','Run npm run build in web before serving')
        return FileResponse(ui/'index.html')
    reconcile_jobs(revoke_pending=True)
    app.add_middleware(RequestLimit,origin=origin,auth=auth)
    return app


def serve(registry,port=8765):
    import uvicorn
    if not 1024<=port<=65535:raise CadendaError('PORT_INVALID','Use a port between 1024 and 65535')
    root=registry.settings.state_root
    Recorder(root)
    fd=os.open(root/'gui-server.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as guard:
        try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise CadendaError('GUI_ALREADY_RUNNING','This workspace GUI is already running')
        code=secrets.token_urlsafe(32);path=root/'gui-pairing-code.txt'
        if path.exists():path.unlink()
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write(code+'\n')
        print(f'Cadenda: http://127.0.0.1:{port}\nOne-time pairing code: {path} (expires when this server stops)',flush=True)
        try:uvicorn.run(create_app(registry,pairing_code=code,port=port),host='127.0.0.1',port=port,proxy_headers=False,access_log=False)
        finally:path.unlink(missing_ok=True)
