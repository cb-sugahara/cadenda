"""SQLite execution authority; no Issue titles/descriptions/status copies."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .errors import CadendaError
from .models import Run, Artifact
from .security import canonical, digest, identifier, read_bounded, redact

ACTIVE = ('Claimed', 'Running', 'Verifying', 'Interrupted', 'NeedsHuman')
TRANSITIONS = {'Claimed': {'Running','NeedsHuman','Interrupted'}, 'Running': {'Verifying','RetryableFailure','NeedsHuman','Interrupted'},
    'Verifying': {'Review','RetryableFailure','NeedsHuman','Interrupted'}, 'Interrupted': {'NeedsHuman'}, 'NeedsHuman': {'Abandoned'},
    'Review': set(), 'RetryableFailure': set(), 'Abandoned': set()}


class Recorder:
    def __init__(self, state_root: Path, *, initialize=True):
        self.root = state_root
        if initialize: self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.read_only = not initialize
        self.path = self.root / 'orchestrator.sqlite3'
        if self.root.resolve() != self.root or self.path.is_symlink():
            raise CadendaError('PATH_DENIED', 'Recorder path may not follow symlinks')
        if not initialize: return
        if not self.path.exists():
            try: os.close(os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600))
            except FileExistsError: pass
        with closing(self.connect()) as db:
            version = db.execute('pragma user_version').fetchone()[0]
            if version > 1:
                raise CadendaError('SCHEMA_VERSION', 'Recorder is newer than this application')
            db.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, issue_id TEXT NOT NULL,
              state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN(0,1)),
              provider TEXT NOT NULL DEFAULT 'executor', owner TEXT NOT NULL, attempt INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
              heartbeat_at REAL, lease_expires_at REAL, worker_pid INTEGER, pgid INTEGER,
              cancel_requested INTEGER NOT NULL DEFAULT 0, worktree TEXT, base_commit TEXT, commit_id TEXT, snapshot_id TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_worker ON runs(active) WHERE active=1;
            CREATE TABLE IF NOT EXISTS run_events (
              event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
              schema_version INTEGER NOT NULL, occurred_at REAL NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts (
              run_id TEXT NOT NULL REFERENCES runs(run_id), artifact_id TEXT NOT NULL, path TEXT NOT NULL,
              sha256 TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(run_id,artifact_id));
            CREATE TABLE IF NOT EXISTS approvals (
              approval_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), action_hash TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', actor_ref TEXT, decided_at REAL);
            CREATE TABLE IF NOT EXISTS outbox (
              event_id TEXT PRIMARY KEY REFERENCES run_events(event_id), delivered_at REAL, attempts INTEGER NOT NULL DEFAULT 0);
            PRAGMA user_version=1;
            COMMIT;
            ''')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect('file:'+str(self.path)+'?mode=ro' if self.read_only else self.path, uri=self.read_only, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('pragma foreign_keys=ON')
        if not self.read_only:
            db.execute('pragma journal_mode=WAL')
            db.execute('pragma synchronous=FULL')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, run_id, kind, payload):
        event_id = 'evt_' + uuid4().hex
        safe, _ = redact(payload)
        db.execute('INSERT INTO run_events VALUES(?,?,1,?,?,?)', (event_id, run_id, time.time(), kind, json.dumps(safe)))
        db.execute('INSERT INTO outbox(event_id) VALUES(?)', (event_id,))
        return event_id

    def claim(self, project_id, issue_id, provider="executor", *, verification=False):
        identifier(project_id); identifier(issue_id)
        key, owner, now = 'run_' + uuid4().hex, uuid4().hex, time.time()
        try:
            with self.transaction() as db:
                previous = db.execute('SELECT * FROM runs WHERE project_id=? AND issue_id=? ORDER BY created_at DESC LIMIT 1', (project_id,issue_id)).fetchone()
                diagnostic=bool(verification and previous and previous['state']=='Review')
                if previous and previous['state'] not in ('RetryableFailure','Abandoned') and not diagnostic:
                    raise CadendaError('RESUME_REQUIRED', 'Prior run requires review or human recovery; no automatic rerun')
                attempt = previous['attempt'] + 1 if previous and not diagnostic else 1
                if attempt > 3:
                    raise CadendaError('RETRY_LIMIT', 'Initial attempt plus two explicit retries exhausted')
                db.execute('INSERT INTO runs(run_id,project_id,issue_id,state,owner,attempt,created_at,updated_at,heartbeat_at,lease_expires_at,worker_pid) VALUES(?,?,?,\'Claimed\',?,?,?,?,?,?,?)',
                    (key,project_id,issue_id,owner,attempt,now,now,now,now+30,os.getpid()))
                db.execute('UPDATE runs SET provider=? WHERE run_id=?',(provider,key))
                self._event(db,key,'run_claimed',{'attempt':attempt})
        except sqlite3.IntegrityError as exc:
            raise CadendaError('RUN_CONFLICT', 'A developer run is already active on this host') from exc
        return key, owner

    def get(self, key):
        identifier(key)
        with closing(self.connect()) as db:
            row = db.execute('SELECT * FROM runs WHERE run_id=?',(key,)).fetchone()
        if not row: raise CadendaError('RUN_UNKNOWN','Unknown run')
        return dict(row)

    def transition(self, key, owner, state, **fields):
        allowed = {'worktree','base_commit','commit_id','snapshot_id','pgid'}
        if not set(fields) <= allowed: raise ValueError('Invalid run fields')
        with self.transaction() as db:
            row = db.execute('SELECT * FROM runs WHERE run_id=? AND owner=?',(key,owner)).fetchone()
            if not row or state not in TRANSITIONS.get(row['state'],set()):
                raise CadendaError('STATE_CONFLICT','Run state or owner no longer permits this transition')
            if state=='Review' and row['cancel_requested']:
                raise CadendaError('CANCELED','Cancellation takes precedence over review readiness')
            assignments = ','.join(f'{name}=?' for name in fields)
            db.execute('UPDATE runs SET state=?,active=?,updated_at=?'+(','+assignments if assignments else '')+' WHERE run_id=?',
                (state,int(state in ACTIVE),time.time(),*fields.values(),key))
            self._event(db,key,'state_changed',{'from':row['state'],'to':state,**fields})
            if state=='Review':
                action_hash=digest(canonical({'action':'review','commit':fields.get('commit_id'),'issue_id':row['issue_id']}))
                approval_id='approval_'+uuid4().hex
                db.execute('INSERT INTO approvals(approval_id,run_id,action_hash) VALUES(?,?,?)',(approval_id,key,action_hash))
                self._event(db,key,'approval_requested',{'approval_id':approval_id,'action_hash':action_hash})

    def history(self,key):
        run=self.get(key);run.pop('owner',None)
        with closing(self.connect()) as db:
            events=[{'schema_version':row['schema_version'],'event_id':row['event_id'],
                'project_id':run['project_id'],'issue_id':run['issue_id'],'run_id':key,
                'actor':'orchestrator','event_type':row['event_type'],
                'occurred_at':datetime.fromtimestamp(row['occurred_at'],timezone.utc).isoformat(),
                'payload':json.loads(row['payload'])} for row in db.execute('SELECT * FROM run_events WHERE run_id=? ORDER BY occurred_at',(key,))]
            artifacts=[dict(row) for row in db.execute('SELECT artifact_id,path,sha256,kind FROM artifacts WHERE run_id=?',(key,))]
            approvals=[dict(row) for row in db.execute('SELECT approval_id,action_hash,status FROM approvals WHERE run_id=?',(key,))]
        return {'run':run,'events':events,'artifacts':artifacts,'approvals':approvals}

    def heartbeat(self, key, owner, pgid=None):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM runs WHERE run_id=? AND owner=? AND state IN(\'Claimed\',\'Running\',\'Verifying\')',(key,owner)).fetchone()
            if not row: raise CadendaError('STATE_CONFLICT','Worker ownership revoked')
            db.execute('UPDATE runs SET heartbeat_at=?,lease_expires_at=?,pgid=? WHERE run_id=?',(time.time(),time.time()+30,pgid,key))
            return bool(row['cancel_requested'])

    def cancel(self, key):
        self.get(key)
        with self.transaction() as db:
            db.execute('UPDATE runs SET cancel_requested=1 WHERE run_id=? AND active=1',(key,))
            self._event(db,key,'cancel_requested',{})

    def note(self,key,kind,payload):
        self.get(key)
        with self.transaction() as db: return self._event(db,key,kind,payload)

    def request_approval(self,key,action):
        self.get(key)
        action_hash=digest(canonical(action));approval_id='approval_'+uuid4().hex
        with self.transaction() as db:
            db.execute('INSERT INTO approvals(approval_id,run_id,action_hash) VALUES(?,?,?)',(approval_id,key,action_hash))
            self._event(db,key,'approval_requested',{'approval_id':approval_id,'action_hash':action_hash})
        return approval_id

    def artifact(self, key, artifact_id, body: bytes, kind='log'):
        identifier(key); identifier(artifact_id)
        text, _ = redact(body.decode('utf-8',errors='replace'))
        directory = self.root / 'artifacts' / key
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        if directory.resolve() != directory: raise CadendaError('PATH_DENIED','Artifact directory symlink')
        content = text.encode(); path = directory / (artifact_id + '.txt')
        fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as file:
            file.write(content); file.flush(); os.fsync(file.fileno())
        with self.transaction() as db:
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)',(key,artifact_id,str(path),digest(content),kind))
            self._event(db,key,'artifact_recorded',{'artifact_id':artifact_id,'sha256':digest(content)})

    @staticmethod
    def alive(pid):
        if not pid: return False
        try: os.kill(pid,0); return True
        except ProcessLookupError: return False
        except PermissionError: return True

    def recover(self):
        recovered=[]
        with self.transaction() as db:
            rows=db.execute("SELECT * FROM runs WHERE state IN('Claimed','Running','Verifying','Interrupted')").fetchall()
            for row in rows:
                # A stale lease never justifies restarting or killing a possibly reused PID.
                if self.alive(row['worker_pid']): continue
                db.execute("UPDATE runs SET state='NeedsHuman',owner=?,updated_at=? WHERE run_id=?",(uuid4().hex,time.time(),row['run_id']))
                self._event(db,row['run_id'],'orphan_detected',{'pgid':row['pgid'],'worktree':row['worktree']})
                recovered.append(row['run_id'])
            for row in db.execute("SELECT * FROM runs WHERE state='Review'").fetchall():
                action_hash=digest(canonical({'action':'review','commit':row['commit_id'],'issue_id':row['issue_id']}))
                if not db.execute('SELECT 1 FROM approvals WHERE run_id=? AND action_hash=?',(row['run_id'],action_hash)).fetchone():
                    approval_id='approval_'+uuid4().hex
                    db.execute('INSERT INTO approvals(approval_id,run_id,action_hash) VALUES(?,?,?)',(approval_id,row['run_id'],action_hash))
                    self._event(db,row['run_id'],'approval_requested',{'approval_id':approval_id,'action_hash':action_hash,'reconciled':True})
        return recovered

    @staticmethod
    def group_alive(pgid):
        if not pgid: return False
        try: os.killpg(pgid,0); return True
        except ProcessLookupError: return False
        except PermissionError: return True

    def release(self,key):
        row=self.get(key)
        if row['state']!='NeedsHuman' or self.alive(row['worker_pid']) or self.group_alive(row['pgid']):
            raise CadendaError('RECOVERY_BLOCKED','Inspect/stop original processes and preserve worktree before releasing')
        self.transition(key,row['owner'],'Abandoned')

    def catalog_runs(self, project_id, issue_ids=None):
        with closing(self.connect()) as db:
            query='SELECT * FROM runs WHERE project_id=?';values=[project_id]
            if issue_ids:
                query+=' AND issue_id IN ('+','.join('?' for _ in issue_ids)+')';values.extend(issue_ids)
            rows=db.execute(query+' ORDER BY created_at DESC LIMIT 500',values).fetchall()
            result=[]
            for row in rows:
                artifacts=[Artifact(artifact_id=a['artifact_id'],path=a['path'],sha256=a['sha256'],kind=a['kind']) for a in db.execute('SELECT * FROM artifacts WHERE run_id=?',(row['run_id'],))]
                outcome='succeeded' if row['state']=='Review' else ('failed' if row['state']=='RetryableFailure' else 'interrupted' if row['state'] in ('Interrupted','NeedsHuman','Abandoned') else 'unknown')
                result.append(Run(run_id=row['run_id'],issue_id=row['issue_id'],provider=row['provider'],outcome=outcome,
                    ended_at=datetime.fromtimestamp(row['updated_at'],timezone.utc),commit=row['commit_id'],base_commit=row['base_commit'],worktree=row['worktree'],artifacts=artifacts))
            return result

    def flush(self, registry, runner):
        # Single writer transaction spans send+ack. After a crash, reconcile by exact event marker.
        with self.transaction() as db:
            for event in db.execute('SELECT e.*,r.project_id,r.issue_id FROM outbox o JOIN run_events e USING(event_id) JOIN runs r USING(run_id) WHERE o.delivered_at IS NULL ORDER BY e.occurred_at').fetchall():
                project=registry.project(event['project_id']); marker=f"[cadenda-event:{event['event_id']}]"
                prefix=['bd','--sandbox','--directory',str(project.root)]
                code,out,_=runner(prefix+['comments',event['issue_id'],'--json'],project.root)
                if code: raise CadendaError('OUTBOX_FAILED','Could not reconcile Beads comments')
                try: comments=json.loads(out)
                except ValueError as exc: raise CadendaError('OUTBOX_FAILED','Invalid Beads JSON') from exc
                if not isinstance(comments,list) or any(not isinstance(c,dict) or not isinstance(c.get('text'),str) for c in comments): raise CadendaError('OUTBOX_FAILED','Invalid Beads comments')
                text=marker+' '+event['event_type']+' '+event['payload']
                matching=[c for c in comments if c['text'].startswith(marker+' ')]
                if any(c['text']!=text or c.get('issue_id')!=event['issue_id'] for c in matching):
                    raise CadendaError('OUTBOX_CONFLICT','Event marker has conflicting text/issue; human inspection required')
                if not matching:
                    code,ack,_=runner(prefix+['comments','add',event['issue_id'],text,'--json'],project.root)
                    if code: raise CadendaError('OUTBOX_FAILED','Beads event delivery failed')
                    try: body=json.loads(ack)
                    except ValueError as exc: raise CadendaError('OUTBOX_FAILED','Invalid Beads acknowledgement') from exc
                    if not isinstance(body,dict) or body.get('text')!=text or body.get('issue_id')!=event['issue_id']:
                        raise CadendaError('OUTBOX_FAILED','Beads did not acknowledge this exact event')
                db.execute('UPDATE outbox SET delivered_at=?,attempts=attempts+1 WHERE event_id=?',(time.time(),event['event_id']))
