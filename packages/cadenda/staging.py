"""Owner-confirmed local artifact deployment; no source execution or production target."""
from contextlib import closing
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from uuid import uuid4

from .context_builder import ContextBuilder
from .config import Registry
from .coordination import access
from .errors import CadendaError
from .recorder import Recorder
from .security import canonical,digest,read_bounded,safe_relative
from .worker import Worker
from .process import beads_guard


def requirements(context):
    return {s.ref:s.raw_sha256 for s in context.sources if s.ref.startswith(('beads://','receipt://','file://','policy://','config://'))}


def current_config(registry):
    fresh=Registry.load(registry.config_path) if getattr(registry,'config_path',None) else registry
    if fresh.settings.state_root!=registry.settings.state_root or {p.project_id:p.root for p in fresh.settings.projects}!={p.project_id:p.root for p in registry.settings.projects}:raise CadendaError('STAGING_CONFIG','Restart the service after changing registered project locations')
    return fresh


def prepare(registry,key):
    registry=current_config(registry)
    r=Recorder(registry.settings.state_root,initialize=False);row=r.get(key);project=registry.project(row['project_id'])
    if not project.staging_enabled:raise CadendaError('STAGING_DISABLED','Owner must enable local staging for this project')
    if row['state']!='Review' or row['provider']!='antigravity+executor' or not row['commit_id']:raise CadendaError('STAGING_PROOF','A committed, checked and reviewed implementation is required')
    history=r.history(key);states=[e for e in history['events'] if e['event_type']=='state_changed' and e['payload']['to']=='Review'];requested=[e for e in history['events'] if e['event_type']=='approval_requested'];decisions=[e for e in history['events'] if e['event_type']=='human_review_decided']
    if not states or not requested or not decisions:raise CadendaError('STAGING_APPROVAL','Review acknowledgement is required before staging preview')
    binding=states[-1]['payload'].get('review_binding');latest=requested[-1]['payload']['approval_id']
    if not binding:raise CadendaError('STAGING_LEGACY_PROOF','Fresh bound review is required')
    with closing(r.connect()) as db:a=db.execute("SELECT * FROM approvals WHERE approval_id=? AND status='approved'",(latest,)).fetchone()
    if not a or a['action_hash']!=digest(canonical(binding)) or decisions[-1]['payload'].get('approval_id')!=latest or decisions[-1]['payload'].get('verdict')!='approve':raise CadendaError('STAGING_APPROVAL','Only the latest acknowledged review is eligible')
    artifacts={a['artifact_id']:a for a in history['artifacts']};reports=[];proof=None
    for item in binding['artifacts']:
        a=artifacts.get(item['artifact_id'])
        if not a or a['sha256']!=item['sha256']:raise CadendaError('STAGING_PROOF','Bound artifact metadata changed')
        body=read_bounded(r.root,str(Path(a['path']).relative_to(r.root)),2_000_000)
        if digest(body)!=a['sha256']:raise CadendaError('STAGING_PROOF','Bound artifact changed')
        if a['artifact_id'].startswith('bound-review-'):reports.append(json.loads(body))
        if a['artifact_id']=='verification-tree':proof=json.loads(body)
    order={e['payload']['artifact_id']:i for i,e in enumerate(history['events']) if e['event_type']=='artifact_recorded'}
    report_ids=[item['artifact_id'] for item in binding['artifacts'] if item['artifact_id'].startswith('bound-review-')]
    latest_report=max(report_ids,key=lambda name:order.get(name,-1)) if report_ids else None
    if latest_report is not None:
        entry=artifacts[latest_report];reports=[json.loads(read_bounded(r.root,str(Path(entry['path']).relative_to(r.root)),2_000_000))]
    if not reports or not proof or reports[-1].get('commit')!=row['commit_id'] or reports[-1].get('review',{}).get('verdict')!='approved':raise CadendaError('STAGING_PROOF','Independent review proof is missing')
    wt=project.allowed_worktree(Path(row['worktree']));git=Worker(registry).git
    if git(wt,'rev-parse','HEAD')!=row['commit_id'] or git(wt,'status','--porcelain'):raise CadendaError('STALE_EVIDENCE','Candidate worktree changed')
    if proof.get('tree')!=git(wt,'rev-parse',row['commit_id']+'^{tree}') or proof.get('checks')!={c.check_id:digest(canonical(c.argv)) for c in project.checks}:raise CadendaError('STAGING_PROOF','Check configuration/tree does not match')
    ctx=ContextBuilder(registry).build(row['project_id'],row['issue_id'],mode='review',budget_tokens=150000)
    if any(w.code not in {'BUDGET_TRUNCATED','SOURCE_EXCERPT'} for w in ctx.warnings):raise CadendaError('CONTEXT_INCOMPLETE','Resolve requirement warnings before staging')
    current=requirements(ctx)
    if reports[-1].get('requirement_hashes')!=current:raise CadendaError('STALE_REQUIREMENTS','Current requirements differ from the independent review')
    target=registry.settings.state_root/'staging'/project.project_id/(key+'-'+row['commit_id'][:12])
    if target.parent.resolve()!=target.parent:raise CadendaError('PATH_DENIED','Staging root has symlinks')
    plan={'schema_version':1,'operation':'local_stage','run_id':key,'project_id':project.project_id,'issue_id':row['issue_id'],'commit':row['commit_id'],'tree':proof['tree'],'review_action_hash':digest(canonical(binding)),'requirement_hashes':current,'config_hash':digest(canonical(registry.settings.model_dump(mode='json'))),'target':str(target),'executes_source':False,'production':False}
    return {'plan':plan,'fingerprint':digest(canonical(plan))}


def apply(registry,key,expected):
    """Called only after the server's operation-specific owner challenge is consumed."""
    row=Recorder(registry.settings.state_root,initialize=False).get(key)
    with access(registry.settings.state_root),beads_guard(registry.project(row['project_id']).root):
        prepared=prepare(registry,key)
        if prepared['fingerprint']!=expected:raise CadendaError('STALE_STAGE','Preview the current staging plan again')
        plan=prepared['plan'];target=Path(plan['target']);manifest=target/'cadenda-stage.json'
        if target.exists():
            if manifest.is_file():
                existing=json.loads(read_bounded(target,'cadenda-stage.json',1_000_000))
                if existing.get('fingerprint')==expected:
                    actual={str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()}
                    if any(p.is_symlink() for p in target.rglob('*')) or actual!=set(existing['files'])|{'cadenda-stage.json'}:raise CadendaError('STAGING_CORRUPT','Existing release contents changed')
                    for relative,sha in existing['files'].items():
                        if digest(read_bounded(target,relative,8_388_608))!=sha:raise CadendaError('STAGING_CORRUPT','Existing release checksum changed')
                    return {'target':str(target),'already_staged':True}
            raise CadendaError('STAGING_CONFLICT','Existing staging directory is preserved')
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temporary=Path(tempfile.mkdtemp(prefix='.stage-',dir=target.parent))
        archive=temporary/'source.tar'
        r=Recorder(registry.settings.state_root);row=r.get(key)
        try:
            with archive.open('xb') as output:
                proc=subprocess.Popen(['git','-c','core.hooksPath=/dev/null','-C',row['worktree'],'archive','--format=tar',plan['commit']],stdout=output,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,start_new_session=True)
                try:_,err=proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:proc.kill();proc.wait();raise CadendaError('STAGING_TIMEOUT','Archive timed out')
            if proc.returncode or archive.stat().st_size>67_108_864:raise CadendaError('STAGING_ARCHIVE','Invalid or oversized source archive')
            git=Worker(registry).git;tree={}
            for entry in git(Path(row['worktree']),'ls-tree','-r','-z',plan['commit']).split('\0'):
                if not entry:continue
                header,name=entry.split('\t',1);mode,kind,oid=header.split()
                if kind!='blob' or mode not in {'100644','100755'} or any(ord(c)<32 for c in name):raise CadendaError('STAGING_UNSAFE','Unsupported source tree entry')
                tree[name]=oid
            release=temporary/'release';release.mkdir(mode=0o700);total=0;seen=set()
            with tarfile.open(archive) as tar:
                for member in tar:
                    safe_relative(member.name)
                    if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):raise CadendaError('STAGING_UNSAFE','Source archive contains links or special files')
                    if member.name=='cadenda-stage.json':raise CadendaError('STAGING_UNSAFE','Reserved release manifest name')
                    total+=member.size
                    if member.size>8_388_608 or total>67_108_864:raise CadendaError('STAGING_UNSAFE','Source size limit exceeded')
                    path=release/member.name
                    if member.isdir():path.mkdir(parents=True,exist_ok=True,mode=0o700)
                    else:
                        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                        with tar.extractfile(member) as src,path.open('xb') as out:shutil.copyfileobj(src,out)
                        path.chmod(0o600)
                        data=path.read_bytes();oid=tree.get(member.name,'');algorithm=hashlib.sha1 if len(oid)==40 else hashlib.sha256
                        if not oid or algorithm(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=oid:raise CadendaError('STAGING_UNSAFE','Archive changed committed source bytes')
                        seen.add(member.name)
            if seen!=set(tree):raise CadendaError('STAGING_UNSAFE','Archive omitted committed source files')
            # Recheck mutable requirement/approval state before exposing the completed release.
            if prepare(registry,key)['fingerprint']!=expected:raise CadendaError('STALE_STAGE','Staging target changed during copy')
            files={str(p.relative_to(release)):digest(p.read_bytes()) for p in release.rglob('*') if p.is_file()}
            (release/'cadenda-stage.json').write_bytes(canonical({'plan':plan,'fingerprint':expected,'files':files}))
            published=False
            try:
                with r.transaction() as db:
                    if digest(canonical(current_config(registry).settings.model_dump(mode='json')))!=plan['config_hash']:raise CadendaError('STALE_STAGE','Owner configuration changed before publication')
                    current=db.execute('SELECT state,commit_id FROM runs WHERE run_id=?',(key,)).fetchone()
                    latest=db.execute("SELECT payload FROM run_events WHERE run_id=? AND event_type='approval_requested' ORDER BY rowid DESC LIMIT 1",(key,)).fetchone()
                    approved=db.execute("SELECT action_hash,status FROM approvals WHERE approval_id=?",(json.loads(latest['payload'])['approval_id'],)).fetchone() if latest else None
                    if current['state']!='Review' or current['commit_id']!=plan['commit'] or not approved or approved['status']!='approved' or approved['action_hash']!=plan['review_action_hash']:raise CadendaError('STALE_STAGE','Review changed before staging publication')
                    release.rename(target);published=True
                    r._event(db,key,'local_stage_completed',{'fingerprint':expected,'target':str(target),'source_executed':False})
            except BaseException:
                if published:target.rename(release)
                raise
            return {'target':str(target),'already_staged':False,'files':len(files),'source_executed':False}
        finally:shutil.rmtree(temporary)
