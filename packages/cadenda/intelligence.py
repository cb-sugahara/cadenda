"""Phase 4 contracts/routing. Model output never grants authority."""
from typing import Literal
from pydantic import Field, model_validator
from .config import StrictModel, ID
from .errors import CadendaError
from .security import safe_relative


class PolicyProposal(StrictModel):
    status: Literal['proposed']
    summary: str
    rationale: str
    affected_issues: list[ID] = Field(min_length=1)
    suggested_document: str

    @model_validator(mode='after')
    def document(self):
        safe_relative(self.suggested_document)
        if self.suggested_document!='AGENTS.md' and not (self.suggested_document.startswith('docs/adr/') and self.suggested_document.endswith('.md')):
            raise ValueError('Policy proposals target AGENTS.md or docs/adr/*.md only')
        return self


class Task(StrictModel):
    proposal_id: ID
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    depends_on: list[ID]
    risk_class: Literal['read_only','workspace_write','staging','production']


class Plan(StrictModel):
    schema_version: Literal[1]
    summary: str
    tasks: list[Task] = Field(min_length=1,max_length=40)

    @model_validator(mode='after')
    def graph(self):
        nodes={t.proposal_id:t for t in self.tasks}
        if len(nodes)!=len(self.tasks):raise ValueError('Duplicate proposal IDs')
        active,done=set(),set()
        def visit(key):
            if key not in nodes:raise ValueError('Dangling dependency')
            if key in active:raise ValueError('Dependency cycle')
            if key in done:return
            active.add(key)
            for dep in nodes[key].depends_on:visit(dep)
            active.remove(key);done.add(key)
        for key in nodes:visit(key)
        return self


class Review(StrictModel):
    verdict: Literal['approved','changes_requested']
    findings: list[str]
    requirements_checked: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def coherent(self):
        if (self.verdict=='approved' and self.findings) or (self.verdict=='changes_requested' and not self.findings):
            raise ValueError('Verdict and findings conflict')
        return self


def route(role,*,risk='workspace_write',classification='internal',cloud_enabled=False):
    if risk not in {'read_only','workspace_write'} or classification not in {'public','internal'}:
        return 'needs_human'
    if role=='executor':return 'executor'
    if cloud_enabled and role in {'architect','reviewer'}:return 'codex'
    if cloud_enabled and role=='developer':return 'antigravity'
    return 'needs_human'
