"""Quiescent, local-only snapshots. No credentials or automatic restore execution."""
from contextlib import closing
from datetime import datetime,timezone
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from uuid import uuid4
import yaml

from .config import Registry,Settings
from .coordination import access
from .errors import CadendaError
from .process import capture
from .recorder import Recorder
from .security import canonical,digest,read_bounded,redact,safe_relative


def run(argv,cwd):
    env=None
    if argv[0]=='git':env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR'}}|{'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_ATTR_NOSYSTEM':'1'}
    code,out,err=capture(argv,cwd,timeout=120,max_bytes=16_777_216,env=env)
    if code or err.strip():raise CadendaError('BACKUP_COMMAND_FAILED','Local backup command failed; inspect the original project')
    return out


def bd(project,*args):
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR'}}|{'BEADS_DIR':str(project/'.beads'),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'core.hooksPath','GIT_CONFIG_VALUE_0':'/dev/null','GIT_CONFIG_KEY_1':'core.fsmonitor','GIT_CONFIG_VALUE_1':'false'}
    if args[0]=='init':(project/'.beads').mkdir(exist_ok=True,mode=0o700)
    argv=['bd','--sandbox']+([] if args[0]=='init' else ['--directory',str(project)])+list(args)+['--json']
    code,out,err=capture(argv,project,timeout=120,max_bytes=16_777_216,env=env)
    if code:raise CadendaError('BACKUP_COMMAND_FAILED','Local Beads operation failed')
    if args[0]=='init':
        # This CLI version ignores --json for init and prints benign setup warnings.
        status=bd(project,'dolt','status')
        if status.get('mode')!='embedded' or not Path(status.get('data_dir','')).resolve().is_relative_to(project/'.beads'):raise CadendaError('BACKUP_SCOPE','Initialized database is outside the restore project')
        return {'initialized':True}
    try:value=json.loads(out)
    except ValueError:raise CadendaError('BACKUP_COMMAND_FAILED','Invalid Beads acknowledgement')
    if isinstance(value,dict) and (value.get('error') or value.get('unsupported')):raise CadendaError('BACKUP_COMMAND_FAILED','Beads rejected backup operation')
    return value


def plain_copy(source,target):
    """Copy trusted engine output without following links or special files."""
    if source.is_symlink():raise CadendaError('BACKUP_UNSAFE','Backup source has a symlink')
    if source.is_dir():
        target.mkdir(parents=True,exist_ok=True,mode=0o700)
        for child in sorted(source.iterdir()):plain_copy(child,target/child.name)
    elif source.is_file():
        fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            import stat
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise CadendaError('BACKUP_UNSAFE','Special files cannot be backed up')
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with os.fdopen(fd,'rb',closefd=False) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
            target.chmod(0o600)
        finally:os.close(fd)
    else:raise CadendaError('BACKUP_UNSAFE','Source is missing or not a regular file')


def issue_proof(project):
    from .beads_adapter import BeadsAdapter
    a=BeadsAdapter(project);rows=a._json('list','--all','--limit=0');proof={}
    for row in rows:
        issue,_=a.issue(row['id']);comments,_=a.comments(row['id'])
        value=issue.model_dump(exclude={'revision'})
        for key in ('dependencies','dependents'):value[key]=sorted(value[key],key=lambda x:(x['id'],x['dependency_type']))
        proof[issue.id]=digest(canonical({'issue':value,'comments':sorted([c.model_dump() for c in comments],key=lambda x:str(x['id']))}))
    return proof


def quiescent(recorder):
    with closing(recorder.connect()) as db:
        if db.execute('SELECT 1 FROM runs WHERE active=1').fetchone():raise CadendaError('BACKUP_BUSY','Finish/recover active runs before backup')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='gui_jobs'").fetchone() and db.execute("SELECT 1 FROM gui_jobs WHERE state IN ('launching','launched','claimed')").fetchone():raise CadendaError('BACKUP_BUSY','Resolve pending launch admissions before backup')


def validate_native(native):
    if native.parent.resolve()!=native.parent or native.resolve()!=native:raise CadendaError('PATH_DENIED','Native backup destination may not traverse symlinks')
    if native.exists():
        if not native.is_dir():raise CadendaError('PATH_DENIED','Native backup must be a directory')
        for item in native.rglob('*'):
            if item.is_symlink() or not (item.is_file() or item.is_dir()):raise CadendaError('PATH_DENIED','Unsafe native backup entry')


def create(registry):
    state=registry.settings.state_root;r=Recorder(state,initialize=False)
    destination=state/'backups';destination.mkdir(exist_ok=True,mode=0o700)
    if destination.resolve()!=destination:raise CadendaError('PATH_DENIED','Backup root cannot use symlinks')
    key='backup_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid4().hex[:8]
    temporary=Path(tempfile.mkdtemp(prefix='.writing-',dir=destination))
    try:
        with access(state,exclusive=True):
            from .operations import compatible_gui
            compatible_gui(state)
            quiescent(r)
            with closing(r.connect()) as db:
                projects={p.project_id for p in registry.settings.projects}
                if any(row[0] not in projects for row in db.execute('SELECT DISTINCT project_id FROM runs')):raise CadendaError('BACKUP_SCOPE','Register historical projects before a complete backup')
                artifacts=[dict(a) for a in db.execute('SELECT * FROM artifacts')]
            settings=registry.settings.model_dump(mode='json')
            if redact(settings)[1]:raise CadendaError('BACKUP_CONFIG_SECRET','Owner config contains credential patterns; remove inline credentials')
            (temporary/'config.json').write_bytes(canonical(settings))
            path_map={};git_heads={};issue_proofs={}
            for project in registry.settings.projects:
                registry.project(project.project_id)
                engine=bd(project.root,'dolt','status')
                if engine.get('mode')!='embedded' or not Path(engine.get('data_dir','')).resolve().is_relative_to(project.root/'.beads'):raise CadendaError('BACKUP_SCOPE','Only primary embedded project databases are supported')
                issue_proofs[project.project_id]=issue_proof(project)
                prefix=['git','--no-optional-locks','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-C',str(project.root)]
                if run(prefix+['diff','--name-only','HEAD','--'],project.root).strip():raise CadendaError('BACKUP_DIRTY','Commit tracked source changes before backup; untracked files are excluded')
                head=run(prefix+['rev-parse','HEAD'],project.root).strip();git_heads[project.project_id]=head
                catalog=None;body=None
                if project.evidence_catalog:
                    from .models import Catalog
                    anchor,relative=project.artifact_location(project.evidence_catalog)
                    body=read_bounded(anchor,relative,16_777_216);catalog=Catalog.model_validate_json(body).model_dump(mode='json')
                    if catalog['project_id']!=project.project_id:raise CadendaError('BACKUP_SCOPE','Legacy catalog project mismatch')
                bundle=temporary/'git'/(project.project_id+'.bundle');bundle.parent.mkdir(exist_ok=True,mode=0o700)
                with closing(r.connect()) as db:commits={c for row in db.execute('SELECT commit_id,base_commit FROM runs WHERE project_id=?',(project.project_id,)) for c in row if c}
                commits.add(head)
                if catalog:
                    commits.update(value for item in catalog['runs'] for value in (item.get('commit'),item.get('base_commit')) if value)
                if any(not re.fullmatch('[a-f0-9]{40,64}',c) for c in commits):raise CadendaError('BACKUP_CORRUPT','Invalid commit metadata')
                for commit in commits:run(prefix+['cat-file','-e',commit+'^{commit}'],project.root)
                run(prefix+['bundle','create',str(bundle),'--all',*sorted(commits)],project.root)
                # Never overwrite an existing owner-managed backup destination.
                native=state/'native-backups'/project.project_id;native.parent.mkdir(exist_ok=True,mode=0o700)
                validate_native(native)
                status=bd(project.root,'backup','status');expected=native.as_uri()
                if status.get('dolt',{}).get('configured') and status['dolt'].get('backup_url')!=expected:raise CadendaError('BACKUP_DESTINATION_CONFLICT','Existing Beads backup destination is preserved; configure a separate registry for that project')
                if not status.get('dolt',{}).get('configured'):bd(project.root,'backup','init',str(native))
                result=bd(project.root,'backup','sync')
                if result.get('synced') is not True:raise CadendaError('BACKUP_COMMAND_FAILED','Native backup was not confirmed')
                plain_copy(native,temporary/'beads'/project.project_id)
                if issue_proof(project)!=issue_proofs[project.project_id]:raise CadendaError('BACKUP_CHANGED','Beads changed during backup; retry in a quiet window')
                if run(prefix+['rev-parse','HEAD'],project.root).strip()!=head:raise CadendaError('BACKUP_CHANGED','Source changed while backing up')
                if project.evidence_catalog:
                    # Legacy files are copied by explicit catalog authority, never whole auth/home dirs.
                    for run_data in catalog.get('runs',[]):
                        for a in run_data.get('artifacts',[]):
                            original=Path(a['path']);base,rel=project.artifact_location(original);data=read_bounded(base,rel,16_777_216)
                            if digest(data)!=a['sha256']:raise CadendaError('BACKUP_CORRUPT','Legacy artifact changed')
                            relpath='state/legacy/files/'+project.project_id+'/'+a['sha256']+'.txt';target=temporary/relpath;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                            if not target.exists():target.write_bytes(data)
                            path_map[str(original)]=relpath
                    target=temporary/'catalogs'/(project.project_id+'.json');target.parent.mkdir(exist_ok=True,mode=0o700);target.write_bytes(body)
            for a in artifacts:
                original=Path(a['path']);relative=str(original.relative_to(state));data=read_bounded(state,relative,16_777_216)
                if digest(data)!=a['sha256']:raise CadendaError('BACKUP_CORRUPT','Indexed artifact changed')
                relpath='state/artifacts/'+a['run_id']+'/'+a['artifact_id']+'.txt';target=temporary/relpath;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);target.write_bytes(data);path_map[str(original)]=relpath
            contexts=state/'contexts'
            if contexts.exists():
                from .snapshots import SnapshotStore
                for child in sorted(contexts.iterdir()):
                    if not child.name.startswith('ctx_'):continue
                    SnapshotStore(state).load(child.name)
                    plain_copy(child,temporary/'state/contexts'/child.name)
            # Only registered job logs; pairing files, HOME directories and CLI auth are excluded.
            with closing(r.connect()) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='gui_jobs'").fetchone():
                    for row in db.execute('SELECT output_path FROM gui_jobs'):
                        original=Path(row[0]);data=read_bounded(state,str(original.relative_to(state)),2_000_000);relpath='state/gui-jobs/'+original.name;target=temporary/relpath;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);target.write_bytes(data);path_map[str(original)]=relpath
                target=temporary/'state/orchestrator.sqlite3';target.parent.mkdir(exist_ok=True,mode=0o700)
                with closing(sqlite3.connect(target)) as dest:
                    db.backup(dest);dest.execute('PRAGMA journal_mode=DELETE')
            files={str(p.relative_to(temporary)):{'sha256':file_digest(p),'bytes':p.stat().st_size} for p in sorted(temporary.rglob('*')) if p.is_file()}
            manifest={'schema_version':1,'backup_id':key,'created_at':datetime.now(timezone.utc).isoformat(),'original_state':str(state),'git_heads':git_heads,'issue_proofs':issue_proofs,'path_map':path_map,'files':files,'scope':'Native Beads history, committed Git, owner config, indexed artifacts, immutable contexts and registered job logs. No auth credentials or untracked source.'}
            (temporary/'manifest.json').write_bytes(canonical(manifest))
            for p in temporary.rglob('*'):
                if p.is_file():p.chmod(0o600)
            final=destination/key;temporary.rename(final)
        return {'backup_id':key,'path':str(final),'manifest_sha256':digest((final/'manifest.json').read_bytes())}
    finally:
        if temporary.exists():shutil.rmtree(temporary)


def verify(path):
    if path.resolve()!=path:raise CadendaError('BACKUP_UNSAFE','Backup must be canonical')
    manifest=json.loads(read_bounded(path,'manifest.json',16_777_216))
    if any(p.is_symlink() for p in path.rglob('*')):raise CadendaError('BACKUP_UNSAFE','Backup contains a symlink')
    if manifest.get('schema_version')!=1:raise CadendaError('BACKUP_INVALID','Unsupported backup format')
    for relative,meta in manifest['files'].items():
        member=checked_member(path,relative,meta['bytes'])
        if file_digest(member)!=meta['sha256'] or member.stat().st_size!=meta['bytes']:raise CadendaError('BACKUP_CORRUPT','Backup file checksum mismatch')
    actual={str(p.relative_to(path)) for p in path.rglob('*') if p.is_file()}
    if actual!=set(manifest['files'])|{'manifest.json'}:raise CadendaError('BACKUP_CORRUPT','Unexpected or missing backup files')
    return manifest


def checked_member(root,relative,size):
    # Native Dolt backups legitimately contain .dolt directories; do not follow any links.
    p=Path(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:raise CadendaError('BACKUP_UNSAFE','Invalid backup member')
    current=root
    for part in p.parts:
        current=current/part
        if current.is_symlink():raise CadendaError('BACKUP_UNSAFE','Backup member symlink')
    if not current.is_file() or current.stat().st_size>size:raise CadendaError('BACKUP_CORRUPT','Invalid backup member size')
    return current


def file_digest(path):
    h=hashlib.sha256()
    with path.open('rb') as file:
        while chunk:=file.read(1048576):h.update(chunk)
    return h.hexdigest()


def restore(path,destination):
    manifest=verify(path)
    original_settings=Settings.model_validate_json((path/'config.json').read_bytes())
    for relative in manifest['path_map'].values():
        safe_relative(relative)
        if not relative.startswith('state/'):raise CadendaError('BACKUP_UNSAFE','Relocated files must stay in restored state')
    if not destination.is_absolute() or destination.exists() or destination.parent.resolve()!=destination.parent or destination.is_relative_to(path):raise CadendaError('RESTORE_TARGET','Restore only to a new canonical directory')
    destination.mkdir(mode=0o700)
    try:
        settings=original_settings.model_dump(mode='json');old_state=Path(settings['state_root']);new_state=destination/'state'
        plain_copy(path/'state',new_state)
        source_db=Recorder(path/'state',initialize=False);quiescent(source_db)
        with closing(source_db.connect()) as db:
            from .security import identifier
            allowed={p['project_id'] for p in settings['projects']}
            for rid,pid in db.execute('SELECT run_id,project_id FROM runs'):
                identifier(rid)
                if pid not in allowed:raise CadendaError('BACKUP_SCOPE','Unknown restored project')
        root_map={}
        for p in settings['projects']:root_map[p['root']]=str(destination/'projects'/p['project_id'])
        for p in settings['projects']:
            repo=Path(root_map[p['root']]);repo.parent.mkdir(exist_ok=True,mode=0o700)
            # No checkout hooks or source execution. Bundle cloning preserves all advertised refs.
            run(['git','-c','core.hooksPath=/dev/null','clone','--quiet','--no-checkout',str(path/'git'/(p['project_id']+'.bundle')),str(repo)],destination)
            run(['git','-C',str(repo),'checkout','--quiet','--detach',manifest['git_heads'][p['project_id']]],destination)
            scaffold=repo/'.beads'
            if scaffold.exists() or scaffold.is_symlink():
                preserved=destination/'source-scaffolds'/p['project_id'];preserved.parent.mkdir(exist_ok=True,mode=0o700);scaffold.rename(preserved)
            # This Beads version's init safety discovery can inspect an ancestor DB
            # even with BEADS_DIR set. Bootstrap outside source repository ancestry;
            # never bypass its guard or force initialization over a discovered DB.
            with tempfile.TemporaryDirectory(prefix='cadenda-restore-',dir='/private/tmp') as fresh:
                bootstrap=Path(fresh)
                run(['git','init','--quiet',str(bootstrap)],destination)
                bd(bootstrap,'init','--prefix',p['project_id'],'--non-interactive','--skip-agents','--skip-hooks')
                plain_copy(bootstrap/'.beads',repo/'.beads')
            native_copy=destination/'.native-source'/p['project_id'];plain_copy(path/'beads'/p['project_id'],native_copy)
            bd(repo,'backup','restore',str(native_copy),'--force')
            run(['git','-C',str(repo),'cat-file','-e',manifest['git_heads'][p['project_id']]+'^{commit}'],destination)
            from .config import Project
            restored_project=Project(project_id=p['project_id'],root=repo)
            if issue_proof(restored_project)!=manifest['issue_proofs'][p['project_id']]:raise CadendaError('RESTORE_CORRUPT','Restored Beads issues/comments/relations differ')
            # --force targets only a newly initialized disposable restore DB, never a source DB.
            original=p['root'];p['root']=str(repo);p['worktree_roots']=[str(destination/'worktrees'/p['project_id']),str(repo)];p['artifact_roots']=[str(new_state/'artifacts'),str(new_state/'legacy')];p['checks']=[];p['cloud_enabled']=False;p['fm_local_enabled']=False;p['staging_enabled']=False
            p['evidence_catalog']=None
        mapping={old:str(destination/relative) for old,relative in manifest['path_map'].items()}
        with sqlite3.connect(new_state/'orchestrator.sqlite3') as db:
            for old,new in mapping.items():db.execute('UPDATE artifacts SET path=? WHERE path=?',(new,old))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='gui_jobs'").fetchone():
                for old,new in mapping.items():db.execute('UPDATE gui_jobs SET output_path=? WHERE output_path=?',(new,old))
            rows=db.execute('SELECT run_id,project_id,worktree,commit_id FROM runs').fetchall()
            for run_id,project_id,worktree,commit in rows:
                repo=destination/'projects'/project_id
                if commit:
                    wt=destination/'worktrees'/project_id/run_id;wt.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    run(['git','-c','core.hooksPath=/dev/null','-C',str(repo),'worktree','add','--quiet','--detach',str(wt),commit],destination)
                    db.execute('UPDATE runs SET worktree=? WHERE run_id=?',(str(wt),run_id))
                elif worktree:db.execute('UPDATE runs SET worktree=? WHERE run_id=?',(str(repo),run_id))
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CadendaError('RESTORE_CORRUPT','Restored SQLite integrity check failed')
        # Regenerate relocated legacy catalogs, retaining original bytes in the verified snapshot.
        for p in settings['projects']:
            catalog=path/'catalogs'/(p['project_id']+'.json')
            if catalog.exists():
                data=json.loads(catalog.read_text())
                for r in data['runs']:
                    for a in r.get('artifacts',[]):a['path']=mapping[a['path']]
                    if r.get('base_commit'):run(['git','-C',p['root'],'cat-file','-e',r['base_commit']+'^{commit}'],destination)
                    if r.get('commit'):
                        from .security import identifier
                        identifier(r['run_id']);wt=destination/'worktrees'/p['project_id']/('legacy-'+r['run_id']);wt.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                        run(['git','-c','core.hooksPath=/dev/null','-C',p['root'],'worktree','add','--quiet','--detach',str(wt),r['commit']],destination)
                        r['worktree']=str(wt)
                    else:r['worktree']=None
                out=new_state/'legacy'/(p['project_id']+'-catalog.json');out.parent.mkdir(exist_ok=True,mode=0o700);out.write_bytes(canonical(data));p['evidence_catalog']=str(out)
        settings['state_root']=str(new_state);validated=Settings.model_validate(settings)
        config=destination/'projects.yaml';config.write_text(yaml.safe_dump(validated.model_dump(mode='json'),allow_unicode=True));config.chmod(0o600)
        # Historical event bodies and Context Snapshots retain original references and hashes.
        from .snapshots import SnapshotStore
        if (new_state/'contexts').exists():
            for p in (new_state/'contexts').iterdir():SnapshotStore(new_state).load(p.name)
        r=Recorder(new_state,initialize=False)
        with closing(r.connect()) as db:
            for a in db.execute('SELECT path,sha256 FROM artifacts'):
                if digest(read_bounded(new_state,str(Path(a[0]).relative_to(new_state)),16_777_216))!=a[1]:raise CadendaError('RESTORE_CORRUPT','Restored artifact mismatch')
        return {'config':str(config),'backup_id':manifest['backup_id'],'execution_enabled':False,'historical_references_preserved':True}
    except BaseException:
        # Leave a failed restore for inspection; it is never activated or overwritten.
        (destination/'RESTORE_FAILED').write_text('Restore incomplete; do not activate.\n');raise
