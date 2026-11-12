import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from cadenda.intelligence import Plan, Review, route
from cadenda.codex_adapter import CodexAdapter
from cadenda.recorder import Recorder
from cadenda.errors import CadendaError
import cadenda.codex_adapter as module


def task(key='a',deps=None):
    return dict(proposal_id=key,title='task',description='implement',acceptance_criteria=['passes'],depends_on=deps or [],risk_class='workspace_write')


@pytest.mark.parametrize('tasks',[[task(),task()],[task(deps=['missing'])],[task(deps=['b']),task('b',['a'])]])
def test_invalid_graphs_rejected(tasks):
    with pytest.raises(ValidationError):Plan.model_validate(dict(schema_version=1,summary='plan',tasks=tasks))


def test_valid_dag_and_routing():
    assert len(Plan(schema_version=1,summary='plan',tasks=[task(),task('b',['a'])]).tasks)==2
    assert route('reviewer',cloud_enabled=True)=='codex'
    assert route('developer',cloud_enabled=True)=='antigravity'
    assert route('executor')=='executor'
    assert route('developer',cloud_enabled=True,risk='production')=='needs_human'
    assert route('architect',cloud_enabled=True,classification='secret')=='needs_human'
    assert route('architect')=='needs_human'
    assert route('fm_pcc',cloud_enabled=True)=='needs_human'


def test_approval_with_findings_is_invalid():
    with pytest.raises(ValidationError):Review(verdict='approved',findings=['bug'],requirements_checked=['AC'],source_refs=['issue'])


@pytest.mark.parametrize('output',['not json',json.dumps({'type':'turn.completed'}),json.dumps({'type':'turn.failed'})])
def test_invalid_codex_output_never_approved(tmp_path,monkeypatch,output):
    r=Recorder(tmp_path/'state');key,owner=r.claim('p','i');r.transition(key,owner,'Running')
    monkeypatch.setattr(module,'execute',lambda *a,**k:(output,''))
    with pytest.raises(CadendaError):CodexAdapter(r).run(key,owner,tmp_path,'reviewer',Review,{'data':'untrusted'})


def test_codex_success_is_validated_and_persisted(tmp_path,monkeypatch):
    r=Recorder(tmp_path/'state');key,owner=r.claim('p','i');r.transition(key,owner,'Running')
    result={'verdict':'approved','findings':[],'requirements_checked':['AC'],'source_refs':['issue']}
    events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(result)}},{'type':'turn.completed'}]
    monkeypatch.setattr(module,'execute',lambda *a,**k:('\n'.join(map(json.dumps,events)),''))
    assert CodexAdapter(r).run(key,owner,tmp_path,'reviewer',Review,{}).verdict=='approved'
    assert any(a['kind']=='review' for a in r.history(key)['artifacts'])


def test_policy_proposals_never_claim_approval_or_escape_docs():
    from cadenda.intelligence import PolicyProposal
    assert PolicyProposal(status='proposed',summary='rule',rationale='why',affected_issues=['i'],suggested_document='docs/adr/001.md')
    for path in ('../secret','docs/adr/../../secret.md','config/projects.yaml'):
        with pytest.raises((ValidationError,CadendaError)):PolicyProposal(status='proposed',summary='rule',rationale='why',affected_issues=['i'],suggested_document=path)
