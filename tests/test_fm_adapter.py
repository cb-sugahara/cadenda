import json
from pathlib import Path
import pytest
from cadenda.recorder import Recorder
from cadenda.errors import CadendaError
from cadenda.fm_adapter import FMAdapter,validate_code,validate_result,Extraction
import cadenda.fm_adapter as module
from cadenda.intelligence import route


@pytest.fixture
def adapter(tmp_path):
    r=Recorder(tmp_path/'state');key,owner=r.claim('p','i');r.transition(key,owner,'Running')
    return FMAdapter(r),key,owner,tmp_path


def test_local_route_is_explicit_and_never_pcc():
    assert route('worker_local',local_enabled=True)=='fm_system'
    assert route('worker_local')=='needs_human'
    assert route('worker_cloud',cloud_enabled=True)=='needs_human'


@pytest.mark.parametrize('options,text,code',[({'model':'pcc'},'hello','PCC_NOT_AUTHORIZED'),({'classification':'secret'},'hello','DATA_DENIED'),({},'x'*4001,'FM_INPUT_LIMIT'),({},'api_key=sk-abcdefghijklmnopqrstuvwxyz123456','DATA_DENIED')])
def test_policy_blocks_before_provider(adapter,monkeypatch,options,text,code):
    a,key,owner,cwd=adapter
    monkeypatch.setattr(module,'execute',lambda *a,**k:pytest.fail('must not execute'))
    with pytest.raises(CadendaError) as exc:a.run(key,owner,cwd,'classify',text,**options)
    assert exc.value.code==code


def test_invalid_output_retries_once(adapter,monkeypatch):
    a,key,owner,cwd=adapter;calls=[]
    def run(*args,**kwargs):calls.append(args);return ('not JSON' if len(calls)==1 else '{"category":"bug"}'),' '
    monkeypatch.setattr(module,'execute',run)
    assert a.run(key,owner,cwd,'classify','reproducible crash').category=='bug'
    assert len(calls)==2 and all('--model' in c[4] and 'system' in c[4] for c in calls)


def test_two_invalid_outputs_are_not_success(adapter,monkeypatch):
    a,key,owner,cwd=adapter;calls=[]
    monkeypatch.setattr(module,'execute',lambda *args,**kwargs:(calls.append(args) or '{"category":"invalid"}',''))
    with pytest.raises(CadendaError):a.run(key,owner,cwd,'classify','text')
    assert len(calls)==2


def test_grounding_rejects_hallucination_and_prefix():
    for value in ['missing','bd-1']:
        with pytest.raises(CadendaError):validate_result('extract',Extraction(issue_id=value,status='open'),'bd-1.2 open')


@pytest.mark.parametrize('source',[
    'import os\ndef clamp(value,lower,upper): return value',
    'def clamp(value,lower,upper): return __import__("os").system("echo bad")',
    'def clamp(value,lower,upper): return value',
    '@print("bad")\ndef clamp(value,lower,upper): return max(lower,min(value,upper))',
    'def clamp(value,lower,upper=print("bad")): return value',
    'def clamp(value,lower,upper): return (lambda: 1)()',
])
def test_generated_code_is_never_executed_and_invalid_code_rejected(source):
    with pytest.raises(CadendaError):validate_code(source)


def test_safe_generated_code_compiles_and_passes_vectors():
    validate_code('def clamp(value, lower, upper):\n    return max(lower, min(value, upper))\n')


@pytest.mark.parametrize('detail,expected',[('quota exceeded','FM_QUOTA'),('ModelManager unavailable','FM_UNAVAILABLE'),('unknown error','FM_PROCESS_FAILED')])
def test_provider_errors_stop_without_retry_or_fallback(adapter,monkeypatch,detail,expected):
    a,key,owner,cwd=adapter;calls=[]
    def fail(*args,**kwargs):
        calls.append(args)
        a.recorder.artifact(key,args[3]+'-stderr',detail.encode())
        raise CadendaError('EXIT_NONZERO','failed')
    monkeypatch.setattr(module,'execute',fail)
    with pytest.raises(CadendaError) as exc:a.run(key,owner,cwd,'classify','A crash')
    assert exc.value.code==expected and len(calls)==1


@pytest.mark.parametrize('code',['TIMEOUT','CANCELED','OUTPUT_LIMIT','LAUNCH_FAILED'])
def test_execution_limits_propagate_without_retry(adapter,monkeypatch,code):
    a,key,owner,cwd=adapter;calls=[]
    def fail(*args,**kwargs):
        calls.append(args);raise CadendaError(code,'stopped')
    monkeypatch.setattr(module,'execute',fail)
    with pytest.raises(CadendaError) as exc:a.run(key,owner,cwd,'classify','A crash')
    assert exc.value.code==code and len(calls)==1


def test_reference_cannot_override_cli_model_or_flags(adapter,monkeypatch):
    a,key,owner,cwd=adapter;calls=[]
    monkeypatch.setattr(module,'execute',lambda *args,**kwargs:(calls.append(args) or '{"category":"question"}',''))
    a.run(key,owner,cwd,'classify','--model=pcc')
    assert calls[0][4][-2:]==['--','--model=pcc']
    assert calls[0][4][2:4]==['--model','system']


def test_reversed_bounds_keep_specified_clamp_semantics():
    with pytest.raises(CadendaError):validate_code('def clamp(value,lower,upper): return min(upper,max(value,lower))')


def test_extraction_rejects_cross_record_pair():
    with pytest.raises(CadendaError):validate_result('extract',Extraction(issue_id='bd-a',status='open'),'issue_id=bd-a status=closed; issue_id=bd-b status=open')
    validate_result('extract',Extraction(issue_id='bd-a',status='open'),'issue_id=bd-a status=open')


def test_exit_zero_with_failure_diagnostic_is_not_success(adapter,monkeypatch):
    a,key,owner,cwd=adapter;calls=[]
    monkeypatch.setattr(module,'execute',lambda *args,**kwargs:(calls.append(args) or '{"category":"bug"}','error: provider denied'))
    with pytest.raises(CadendaError) as exc:a.run(key,owner,cwd,'classify','A crash')
    assert exc.value.code=='FM_DIAGNOSTIC' and len(calls)==1
