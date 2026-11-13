"""Local operations: bounded restart supervision, non-inference monitoring, launchd."""
from contextlib import closing,contextmanager
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import signal
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

from .coordination import access
from .errors import CadendaError
from .process import capture,beads_capture
from .recorder import Recorder
from .security import canonical,digest,read_bounded,redact

LABEL='com.cadenda.local'


def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.parent.resolve()!=path.parent:raise CadendaError('PATH_DENIED','Operations directory symlink')
    temp=path.with_name('.'+path.name+'-'+uuid4().hex)
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(canonical(value));f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def compatible_gui(state):
    fd=os.open(state/'gui-server.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return
        except BlockingIOError:pass
        try:
            meta=json.loads(f.read(4096));port=meta['port']
            if meta.get('protocol')!=1 or not isinstance(port,int) or not 1024<=port<=65535:raise ValueError()
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz',timeout=2) as response:value=json.loads(response.read(4096))
            if value.get('protocol')!=1 or value.get('workspace')!=digest(str(state).encode()) or value.get('instance')!=meta.get('instance'):raise ValueError()
        except (OSError,ValueError,KeyError):raise CadendaError('GUI_RESTART_REQUIRED','Restart the GUI with maintenance-aware code before backup')


def health(registry,*,probe=False):
    state=registry.settings.state_root;r=Recorder(state,initialize=False);alerts=[];now=time.time()
    usage=shutil.disk_usage(state)
    if usage.free<1_073_741_824:alerts.append({'code':'DISK_LOW','free_bytes':usage.free})
    with closing(r.connect()) as db:
        rows=[dict(row) for row in db.execute('SELECT * FROM runs')]
        for row in rows:
            if row['project_id'] not in {p.project_id for p in registry.settings.projects}:continue
            if row['active'] and (not Recorder.alive(row['worker_pid']) or row['state'] in {'NeedsHuman','Interrupted'}):alerts.append({'code':'RUN_NEEDS_HUMAN','project_id':row['project_id'],'run_id':row['run_id']})
            elif row['active'] and row['heartbeat_at'] and now-row['heartbeat_at']>90:alerts.append({'code':'HEARTBEAT_STALE','project_id':row['project_id'],'run_id':row['run_id']})
        events=db.execute("SELECT e.run_id,r.project_id,e.payload FROM run_events e JOIN runs r USING(run_id) WHERE e.event_type='command_finished' AND e.occurred_at>?",(now-86400,)).fetchall()
        for e in events:
            if e['project_id'] not in {p.project_id for p in registry.settings.projects}:continue
            payload=json.loads(e['payload'])
            if payload.get('reason'):alerts.append({'code':'COMMAND_FAILED','project_id':e['project_id'],'run_id':e['run_id'],'reason':payload['reason']})
        artifacts=db.execute('SELECT a.*,r.project_id FROM artifacts a JOIN runs r USING(run_id)').fetchall()
        for a in artifacts:
            if a['project_id'] not in {p.project_id for p in registry.settings.projects}:continue
            try:
                data=read_bounded(state,str(Path(a['path']).relative_to(state)),16_777_216)
                if digest(data)!=a['sha256']:raise ValueError()
                if a['artifact_id'].endswith('stderr'):
                    text=data.decode(errors='replace').lower()
                    kind='AUTH_REQUIRED' if any(s in text for s in ['unauthorized','login required','authentication failed','not authenticated']) else 'QUOTA' if 'quota' in text else 'NETWORK' if any(s in text for s in ['network unreachable','connection refused','connection timed out']) else None
                    if kind:alerts.append({'code':kind,'project_id':a['project_id'],'run_id':a['run_id']})
            except (OSError,ValueError,CadendaError):alerts.append({'code':'ARTIFACT_UNAVAILABLE','project_id':a['project_id'],'run_id':a['run_id'],'artifact_id':a['artifact_id']})
    projects=[]
    for project in registry.settings.projects:
        try:
            code,out,err=beads_capture(['bd','--readonly','--sandbox','--directory',str(project.root),'dolt','status','--json'],project.root,timeout=10)
            value=json.loads(out)
            if code or value.get('error') or value.get('mode')!='embedded' or not Path(value.get('data_dir','')).resolve().is_relative_to(project.root/'.beads'):raise ValueError()
            projects.append({'project_id':project.project_id,'beads':'embedded','migration_required':False})
        except (OSError,ValueError,CadendaError):alerts.append({'code':'BEADS_UNAVAILABLE','project_id':project.project_id})
    backups=state/'backups';last=None
    if backups.exists():
        from .backup import verify
        for p in sorted(backups.glob('backup_*'),reverse=True):
            try:manifest=verify(p);last=manifest['created_at'];break
            except (OSError,ValueError,CadendaError):alerts.append({'code':'BACKUP_CORRUPT','backup_id':p.name})
    if not last or now-datetime.fromisoformat(last).timestamp()>86400:alerts.append({'code':'BACKUP_DUE'})
    providers={'inference_performed':False,'authentication':'not probed; derived alerts only','pcc':'disabled'}
    if probe:
        if any(p.cloud_enabled for p in registry.settings.projects):
            try:
                code,out,err=capture(['codex','login','status'],state,timeout=10,max_bytes=65536)
                providers['codex_auth']='authenticated' if code==0 and 'logged in' in (out+err).lower() else 'required' if code else 'unknown'
            except (OSError,CadendaError):providers['codex_auth']='unavailable'
        providers['antigravity_auth']='no non-inference status command; detect recorded failures'
        try:
            code,out,err=capture(['/usr/bin/fm','models'],state,timeout=10,max_bytes=65536)
            providers['fm_system']='available' if code==0 and 'system' in out and '✓' in out else 'unavailable'
        except (OSError,CadendaError):providers['fm_system']='unavailable'
    report={'schema_version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'alerts':redact(alerts)[0],'projects':projects,'disk_free_bytes':usage.free,'last_verified_backup':last,'providers':providers,'service':service_status(registry)}
    atomic(state/'ops/health.json',report)
    return report


@contextmanager
def restart_lock(state):
    dest=state/'ops';dest.mkdir(exist_ok=True,mode=0o700)
    if dest.resolve()!=dest:raise CadendaError('PATH_DENIED','Supervisor directory symlink')
    fd=os.open(dest/'restarts.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        yield


def reset(registry):
    with restart_lock(registry.settings.state_root):
        atomic(registry.settings.state_root/'ops/restarts.json',{'generation':uuid4().hex,'attempts':[]})
    return {'restart_budget_reset':True}


def admit_start(state,max_restarts,window,now):
    with restart_lock(state):
        ledger=state/'ops/restarts.json'
        data=json.loads(read_bounded(state,'ops/restarts.json',65536)) if ledger.exists() else {'generation':uuid4().hex,'attempts':[]}
        attempts=[t for t in data['attempts'] if now-t<window]
        if data.get('blocked') or len(attempts)>=max_restarts:
            atomic(ledger,{'generation':data['generation'],'attempts':attempts,'blocked':True})
            return data['generation'],False
        attempts.append(now)
        atomic(ledger,{'generation':data['generation'],'attempts':attempts,'blocked':len(attempts)>=max_restarts})
        return data['generation'],True


def supervise(registry,port=8765,*,max_restarts=3,window=600,stop=None,launcher=subprocess.Popen,poll_seconds=1):
    state=registry.settings.state_root;Recorder(state);dest=state/'ops';dest.mkdir(exist_ok=True,mode=0o700)
    if dest.resolve()!=dest:raise CadendaError('PATH_DENIED','Supervisor directory symlink')
    fd=os.open(dest/'supervisor.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as guard:
        try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise CadendaError('SERVICE_RUNNING','Supervisor already running')
        stopped=False
        def halt(*args):
            nonlocal stopped;stopped=True
        previous={}
        if stop is None:
            for s in (signal.SIGTERM,signal.SIGINT):previous[s]=signal.signal(s,halt)
        child=None;next_health=0;exit_reason='operator_stop'
        try:
            while not stopped and not (stop and stop()):
                now=time.time();ledger=dest/'restarts.json'
                if child is not None and child.poll() is not None:
                    atomic(dest/'service.json',{'state':'child_exited','exit_code':child.returncode,'at':now});child=None
                if child is None:
                    try:
                        with access(state):pass
                    except CadendaError:
                        time.sleep(poll_seconds);continue
                    generation,admitted=admit_start(state,max_restarts,window,now)
                    if not admitted:
                        # Remain idle until an explicit reset; no launchd respawn loop.
                        atomic(dest/'service.json',{'state':'needs_human','reason':'restart_budget','at':now})
                        # Freeze exhausted budget across time, not just within the rolling window.
                        while not stopped and not (stop and stop()):
                            current=json.loads(read_bounded(state,'ops/restarts.json',65536))
                            if current.get('generation')!=generation:break
                            time.sleep(poll_seconds)
                        continue
                    log=dest/'service-log.txt'
                    if log.exists() and log.stat().st_size>2_000_000:log.rename(dest/'service-log.previous.txt')
                    with log.open('ab') as output:
                        try:child=launcher([sys.executable,'-I','-m','cadenda.cli','--config',str(registry.config_path),'serve','--port',str(port)],cwd=registry.config_path.parent.parent,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True,env={k:v for k,v in os.environ.items() if k in {'HOME','PATH','LANG','LC_ALL','TMPDIR'}})
                        except OSError:
                            atomic(dest/'service.json',{'state':'launch_failed','at':now});time.sleep(poll_seconds);continue
                    atomic(dest/'service.json',{'state':'running','pid':child.pid,'at':now})
                if now>=next_health:
                    try:health(registry)
                    except (OSError,ValueError,CadendaError,sqlite3.Error):atomic(dest/'monitor-error.json',{'at':now,'state':'needs_human'})
                    next_health=now+300
                time.sleep(poll_seconds)
        except (OSError,ValueError,CadendaError,sqlite3.Error):
            exit_reason='supervisor_error'
            if child is not None and child.poll() is None:child.terminate()
            try:atomic(dest/'service.json',{'state':'needs_human','reason':'supervisor_error','at':time.time()})
            except OSError:pass
            while not stopped and not (stop and stop()):time.sleep(poll_seconds)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:child.wait(timeout=20)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
            atomic(dest/'service.json',{'state':'stopped','reason':exit_reason,'at':time.time()})
            for sig,handler in previous.items():signal.signal(sig,handler)
    return {'stopped':True}


def launch_plan(registry,port=8765):
    Recorder(registry.settings.state_root)
    if not 1024<=port<=65535:raise CadendaError('PORT_INVALID','Invalid local port')
    root=registry.settings.state_root/'ops';root.mkdir(exist_ok=True,mode=0o700)
    plan={'Label':LABEL,'ProgramArguments':[sys.executable,'-I','-m','cadenda.cli','--config',str(registry.config_path),'ops','supervise','--port',str(port)],'WorkingDirectory':str(registry.config_path.parent.parent),'RunAtLoad':True,'KeepAlive':False,'ThrottleInterval':60,'ExitTimeOut':30,'EnvironmentVariables':{'HOME':str(Path.home()),'PATH':os.environ.get('PATH','/opt/homebrew/bin:/usr/bin:/bin')},'StandardOutPath':str(root/'launchd.stdout.txt'),'StandardErrorPath':str(root/'launchd.stderr.txt')}
    path=root/(LABEL+'.plist');path.write_bytes(plistlib.dumps(plan));path.chmod(0o600)
    return {'path':str(path),'label':LABEL,'sha256':digest(path.read_bytes()),'scope':'per-user GUI LaunchAgent, loopback only'}


def install(registry,port=8765):
    plan=launch_plan(registry,port);source=Path(plan['path']);destination=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist');destination.parent.mkdir(exist_ok=True)
    if destination.parent.resolve()!=destination.parent or destination.is_symlink():raise CadendaError('PATH_DENIED','LaunchAgent symlinks are not supported')
    if destination.exists() and destination.read_bytes()!=source.read_bytes():raise CadendaError('SERVICE_CONFIG_CONFLICT','Existing LaunchAgent differs; preserve it and inspect the prepared plan')
    if not destination.exists():plain=source.read_bytes();fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.write(fd,plain);os.close(fd)
    code,_,_=capture(['/bin/launchctl','print',f'gui/{os.getuid()}/{LABEL}'],registry.config_path.parent.parent)
    if code==0:return {'installed':str(destination),'label':LABEL,'already_loaded':True}
    code,out,err=capture(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(destination)],registry.config_path.parent.parent)
    if code:raise CadendaError('SERVICE_BOOTSTRAP','LaunchAgent registration failed; no permission bypass attempted')
    return {'installed':str(destination),'label':LABEL}


def service_status(registry):
    p=registry.settings.state_root/'ops/service.json'
    return json.loads(read_bounded(registry.settings.state_root,'ops/service.json',65536)) if p.exists() else {'state':'not_started'}
