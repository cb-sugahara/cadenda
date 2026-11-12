"""Owner-invoked planning/review. Review approval is not human authorization."""
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .beads_adapter import BeadsAdapter
from .config import Registry
from .context_builder import ContextBuilder
from .codex_adapter import CodexAdapter
from .errors import CadendaError
from .intelligence import Plan, PolicyProposal, Review, route
from .recorder import Recorder
from .security import canonical, digest, read_bounded
from .worker import Worker


class Pipeline:
    def __init__(self,registry):
        self.registry=registry;self.recorder=Recorder(registry.settings.state_root)

    def allowed(self,project,role):
        if route(role,cloud_enabled=project.cloud_enabled)=='needs_human':
            raise CadendaError('CLOUD_DISABLED','Owner must explicitly enable cloud for this project')

    def architect(self,project_id,issue_id,*,policy=False):
        project=self.registry.project(project_id);self.allowed(project,'architect')
        context=ContextBuilder(self.registry).build(project_id,issue_id,budget_tokens=150000)
        if any(w.code not in {'BUDGET_TRUNCATED','SOURCE_EXCERPT','RUN_STORE_UNCONFIGURED'} for w in context.warnings):raise CadendaError('CONTEXT_INCOMPLETE','Resolve context warnings before cloud planning')
        key,owner=self.recorder.claim(project_id,issue_id,provider='codex-architect',verification=True)
        try:
            self.recorder.transition(key,owner,'Running',snapshot_id=context.snapshot_id)
            contract=PolicyProposal if policy else Plan
            plan=CodexAdapter(self.recorder).run(key,owner,project.root,'architect',contract,{'context':context.markdown(),'requested_output':'Policy proposal only, never an approved decision; identify affected issues and suggest an ADR/AGENTS path.' if policy else 'Task decomposition proposal only.'})
            self.recorder.artifact(key,'plan-proposal',plan.model_dump_json().encode(),'report')
            self.recorder.transition(key,owner,'Verifying')
            self.recorder.transition(key,owner,'Review')
            return {'run_id':key,'proposal':plan.model_dump(),'human_approval_required':True,'proposal_sha256':digest(canonical(plan.model_dump()))}
        except BaseException:
            self.recorder.transition(key,owner,'NeedsHuman');raise

    def review(self,key):
        row=self.recorder.get(key);project=self.registry.project(row['project_id']);self.allowed(project,'reviewer')
        if row['provider']!='antigravity+executor' or not row['commit_id']:raise CadendaError('NOT_IMPLEMENTATION','Only committed implementation runs can be reviewed')
        worktree=project.allowed_worktree(Path(row['worktree']));git=Worker(self.registry).git
        if git(worktree,'rev-parse','HEAD')!=row['commit_id'] or git(worktree,'status','--porcelain'):raise CadendaError('STALE_EVIDENCE','Worktree differs from recorded commit')
        issue,raw=BeadsAdapter(project).issue(row['issue_id'])
        history=self.recorder.history(key);checks=[]
        for check in project.checks:
            starts=[e for e in history['events'] if e['event_type']=='command_started' and e['payload'].get('name')=='check-'+check.check_id]
            ends=[e for e in history['events'] if e['event_type']=='command_finished' and e['payload'].get('name')=='check-'+check.check_id]
            if not starts or starts[-1]['payload']['argv_sha256']!=digest(canonical(check.argv)) or not ends or ends[-1]['payload'].get('exit_code')!=0 or ends[-1]['payload'].get('reason'):
                raise CadendaError('CHECK_EVIDENCE_MISSING','Successful evidence for every configured check is required')
            for stream in ('stdout','stderr'):
                artifact=next((a for a in history['artifacts'] if a['artifact_id']=='check-'+check.check_id+'-'+stream),None)
                if artifact is None:raise CadendaError('CHECK_EVIDENCE_MISSING','Missing check artifact')
                body=read_bounded(self.recorder.root,str(Path(artifact['path']).relative_to(self.recorder.root)),2_000_000)
                if digest(body)!=artifact['sha256']:raise CadendaError('ARTIFACT_CORRUPT','Check artifact changed')
                checks.append({'check':check.check_id,'stream':stream,'text':body.decode(),'sha256':artifact['sha256']})
        capsule=next((a for a in history['artifacts'] if a['artifact_id']=='verification-tree'),None)
        if capsule is None:raise CadendaError('CHECK_EVIDENCE_MISSING','Candidate tree proof missing')
        raw_capsule=read_bounded(self.recorder.root,str(Path(capsule['path']).relative_to(self.recorder.root)),2000000)
        if digest(raw_capsule)!=capsule['sha256']:raise CadendaError('ARTIFACT_CORRUPT','Candidate proof changed')
        proof=json.loads(raw_capsule)
        if proof.get('tree')!=git(worktree,'rev-parse',row['commit_id']+'^{tree}') or proof.get('checks')!={c.check_id:digest(canonical(c.argv)) for c in project.checks}:raise CadendaError('STALE_EVIDENCE','Checks do not cover this committed source')
        if not checks:raise CadendaError('CHECK_EVIDENCE_MISSING','No checks')
        context=ContextBuilder(self.registry).build(row['project_id'],row['issue_id'],mode='review',budget_tokens=150000)
        if any(w.code not in {'BUDGET_TRUNCATED','SOURCE_EXCERPT'} for w in context.warnings):raise CadendaError('CONTEXT_INCOMPLETE','Review context is incomplete')
        requirement_ids=lambda c:{s.ref:s.raw_sha256 for s in c.sources if s.ref.startswith(('beads://','receipt://','file://','policy://','config://'))}
        requirement_hashes=requirement_ids(context)
        prior_reports=[]
        for event in history['events']:
            if event['event_type']=='repair_started':
                prior=self.recorder.history(event['payload']['previous_run'])
                if prior['run']['project_id']!=row['project_id'] or prior['run']['issue_id']!=row['issue_id']:raise CadendaError('REPAIR_INVALID','Prior review scope mismatch')
                for a in prior['artifacts']:
                    if a['artifact_id'].startswith('bound-review-'):
                        b=read_bounded(self.recorder.root,str(Path(a['path']).relative_to(self.recorder.root)),2000000)
                        if digest(b)!=a['sha256']:raise CadendaError('ARTIFACT_CORRUPT','Prior review changed')
                        prior_reports.append(json.loads(b))
        owner=uuid4().hex
        try:
            with self.recorder.transaction() as db:
                updated=db.execute("UPDATE runs SET state='Verifying',active=1,owner=?,worker_pid=?,cancel_requested=0,pgid=NULL WHERE run_id=? AND state='Review' AND active=0",(owner,os.getpid(),key))
                if updated.rowcount!=1:raise CadendaError('STATE_CONFLICT','Run is not awaiting review')
                self.recorder._event(db,key,'review_started',{})
        except sqlite3.IntegrityError as exc:raise CadendaError('RUN_CONFLICT','Another worker is active') from exc
        try:
            patch=git(worktree,'diff','--no-ext-diff','--no-textconv',row['base_commit'],row['commit_id'],'--')
            evidence={'issue':raw,'context':context.markdown(),'commit':row['commit_id'],'diff':patch,'independent_checks':checks,'execution_record':history,'prior_rejections':prior_reports}
            result=CodexAdapter(self.recorder).run(key,owner,worktree,'reviewer',Review,evidence)
            fresh_registry=Registry.load(self.registry.config_path) if getattr(self.registry,'config_path',None) else self.registry
            refreshed=ContextBuilder(fresh_registry).build(row['project_id'],row['issue_id'],mode='review',budget_tokens=150000)
            if requirement_ids(refreshed)!=requirement_hashes:raise CadendaError('STALE_REQUIREMENTS','Requirement sources changed during review')
            current,current_raw=BeadsAdapter(project).issue(row['issue_id'])
            if digest(canonical(current_raw))!=digest(canonical(raw)) or current.revision!=issue.revision or git(worktree,'rev-parse','HEAD')!=row['commit_id'] or git(worktree,'status','--porcelain'):raise CadendaError('STALE_EVIDENCE','Source/requirements changed during review')
            report={'review':result.model_dump(),'commit':row['commit_id'],'issue_revision':issue.revision,'evidence_sha256':digest(canonical(evidence))}
            self.recorder.artifact(key,'bound-review-'+uuid4().hex[:12],canonical(report),'review')
            self.recorder.transition(key,owner,'Review' if result.verdict=='approved' else 'RetryableFailure',commit_id=row['commit_id'],pgid=None)
            return report
        except BaseException:
            self.recorder.transition(key,owner,'NeedsHuman',pgid=None);raise

    def run(self,project_id,issue_id,max_fixes=2):
        if not 0<=max_fixes<=2:raise CadendaError('RETRY_LIMIT','At most two fixes')
        project=self.registry.project(project_id);self.allowed(project,'developer')
        reports=[];previous=None
        for attempt in range(max_fixes+1):
            # Each retry is a new isolated implementation; previous diffs and review remain intact.
            row=Worker(self.registry).start(project_id,issue_id,repair_of=previous)
            report=self.review(row['run_id']);reports.append(report);previous=row['run_id']
            if report['review']['verdict']=='approved':return {'run_id':row['run_id'],'reports':reports,'human_approval_required':True}
        self.recorder.note(row['run_id'],'human_needed',{'reason':'review_fix_limit'})
        return {'run_id':row['run_id'],'reports':reports,'human_needed':True}
