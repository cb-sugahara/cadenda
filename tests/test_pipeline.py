from types import SimpleNamespace
import pytest
import cadenda.pipeline as module
from cadenda.errors import CadendaError
from cadenda.pipeline import Pipeline


def test_pipeline_caps_fix_loop_and_carries_previous_run(monkeypatch):
    calls=[];notes=[]
    pipeline=object.__new__(Pipeline)
    pipeline.registry=SimpleNamespace(project=lambda key:SimpleNamespace(cloud_enabled=True))
    pipeline.recorder=SimpleNamespace(note=lambda *args:notes.append(args))
    def start(project,issue,repair_of=None):
        calls.append(repair_of);return {'run_id':'r'+str(len(calls))}
    monkeypatch.setattr(module,'Worker',lambda registry:SimpleNamespace(start=start))
    pipeline.review=lambda key:{'review':{'verdict':'changes_requested','findings':['fix']}}
    result=pipeline.run('p','i',2)
    assert calls==[None,'r1','r2'] and result['human_needed']
    assert notes[0][1]=='human_needed'
    with pytest.raises(CadendaError):pipeline.run('p','i',3)


def test_pipeline_stops_on_approved_without_claiming_human_consent(monkeypatch):
    pipeline=object.__new__(Pipeline)
    pipeline.registry=SimpleNamespace(project=lambda key:SimpleNamespace(cloud_enabled=True))
    monkeypatch.setattr(module,'Worker',lambda r:SimpleNamespace(start=lambda *a,**k:{'run_id':'r1'}))
    pipeline.review=lambda key:{'review':{'verdict':'approved'}}
    assert pipeline.run('p','i')['human_approval_required'] is True


def test_cloud_disabled_never_calls_worker(monkeypatch):
    pipeline=object.__new__(Pipeline);pipeline.registry=SimpleNamespace(project=lambda key:SimpleNamespace(cloud_enabled=False))
    monkeypatch.setattr(module,'Worker',lambda r:pytest.fail('must not start cloud'))
    with pytest.raises(CadendaError) as exc:pipeline.run('p','i')
    assert exc.value.code=='CLOUD_DISABLED'
