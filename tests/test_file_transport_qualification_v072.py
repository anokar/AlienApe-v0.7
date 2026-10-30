import asyncio,json,tempfile
import pytest
from alienape.backends import MockBackend,tool_reply
from alienape.completion import ModelResponse,CompletionMetadata
from alienape.config import Settings
from alienape.experience import ExperienceStore,ModelFingerprint
from alienape.onboarding import file_transport_qualification_tasks
from alienape.routing import ModelRegistry
from alienape.training import TrainingRunner,plan_for
from test_routing import profile


def run_case(tmp_path,monkeypatch,mode,response,index=0):
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    settings=Settings();p=profile('candidate','coding','low',tool_mode=mode);store=ExperienceStore(tmp_path/'e.sqlite3')
    fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    runner=TrainingRunner(ModelRegistry([p]),settings,store,[p.id],scope='file_transport');runner.planned=1
    task=plan_for(p,file_transport_qualification_tasks(),'file_transport')[index]
    result=asyncio.run(runner.measure(p,MockBackend([response],delay=0),fp,task,{}))
    assert not (tmp_path/'target_1.py').exists()
    return result


def text(value,reason='stop'):
    return ModelResponse({'role':'assistant','content':value},completion=CompletionMetadata(reason))


def native(path,content):
    return ModelResponse(tool_reply('write_file',{'path':path,'content':content}),completion=CompletionMetadata('tool_calls'))


def test_file_content_success(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'none',text(json.dumps({'content':'def solve(a,b): return a+b'})))
    assert (r['protocol_status'],r['signal'])==('VERIFIED_SUCCESS','VERIFIED_SUCCESS')


def test_file_content_valid_protocol_invalid_python(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'none',text(json.dumps({'content':'def broken('})))
    assert (r['protocol_status'],r['signal'])==('VERIFIED_SUCCESS','VERIFIED_FAILURE')


def test_file_content_fence_is_protocol_failure_only(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'none',text('```python\ndef solve(a,b): return a+b\n```'))
    assert (r['protocol_status'],r['signal'])==('VERIFIED_FAILURE','UNKNOWN')


def test_file_content_truncated_is_resource_neutral(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'none',text('{"content":',reason='length'))
    assert r['protocol_status'] is None and r['signal'] in {'UNKNOWN','RESOURCE_FAILURE'}


@pytest.mark.parametrize('code,expected',[('def solve(a,b): return a+b','VERIFIED_SUCCESS'),('def broken(','VERIFIED_FAILURE')])
def test_native_exact_target_protocol_separate_from_quality(tmp_path,monkeypatch,code,expected):
    r=run_case(tmp_path,monkeypatch,'native',native('target_1.py',code))
    assert (r['protocol_status'],r['signal'])==('VERIFIED_SUCCESS',expected)


@pytest.mark.parametrize('path',['other.py','../outside.py'])
def test_native_wrong_target_fails_without_write(tmp_path,monkeypatch,path):
    r=run_case(tmp_path,monkeypatch,'native',native(path,'def solve(a,b): return a+b'))
    assert (r['protocol_status'],r['signal'])==('VERIFIED_FAILURE','UNKNOWN')
    assert r['tool_observation']['denied'] and not r['tool_observation']['write_performed']


def test_native_missing_tool_fails_protocol(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'native',text('not a tool call'))
    assert (r['protocol_status'],r['signal'])==('VERIFIED_FAILURE','UNKNOWN')


def test_transport_plans_match_profile_mode():
    tasks=file_transport_qualification_tasks()
    assert len(plan_for(profile('a','coding','low',tool_mode='none'),tasks,'file_transport'))==5
    assert len(plan_for(profile('b','coding','low',tool_mode='native'),tasks,'file_transport'))==5
    assert plan_for(profile('c','coding','low',tool_mode='json_fallback'),tasks,'file_transport')==[]


def test_native_fenced_content_is_not_valid_python(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'native',native('target_1.py','```python\ndef solve(a,b): return a+b\n```'))
    assert (r['protocol_status'],r['signal'])==('VERIFIED_SUCCESS','VERIFIED_FAILURE')


def test_native_output_limit_is_neutral_resource(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'native',text('unfinished',reason='length'))
    assert r['protocol_status'] is None
    assert r['signal']=='RESOURCE_FAILURE'
    assert r['failure_type']=='RESOURCE_OUTPUT_LIMIT'


def test_timeout_has_no_protocol_failure(tmp_path,monkeypatch):
    import httpx
    class TimeoutBackend(MockBackend):
        async def chat(self,*args,**kwargs):raise httpx.TimeoutException('timeout')
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    settings=Settings();p=profile('candidate','coding','low',tool_mode='none');store=ExperienceStore(tmp_path/'e.sqlite3')
    fp=ModelFingerprint.for_profile(p);store.register(p,fp)
    runner=TrainingRunner(ModelRegistry([p]),settings,store,[p.id],scope='file_transport');runner.planned=1
    task=plan_for(p,file_transport_qualification_tasks(),'file_transport')[0]
    result=asyncio.run(runner.measure(p,TimeoutBackend(delay=0),fp,task,{}))
    assert result['protocol_status'] is None and result['signal']=='UNKNOWN' and result['failure_type']=='TIMEOUT'


def test_native_truncated_malformed_call_is_resource_neutral(tmp_path,monkeypatch):
    response=native('../outside.py','def solve(a,b): return a+b')
    response.completion.finish_reason='length'
    r=run_case(tmp_path,monkeypatch,'native',response)
    assert r['protocol_status'] is None and r['signal']=='RESOURCE_FAILURE'
    assert r['failure_type']=='RESOURCE_OUTPUT_LIMIT'


def test_fifth_case_is_versioned_and_fixed_for_each_transport():
    tasks=file_transport_qualification_tasks()
    fifth=[t for t in tasks if t.id.endswith('-5')]
    assert len(fifth)==2
    assert all(t.benchmark_catalog_version=='transport-v2' and t.expected=={'target':'target_5.py','symbol':'-'} for t in fifth)
    assert all('minus' in t.prompt for t in fifth)


def test_fifth_case_target_is_not_treated_as_missing_input(tmp_path,monkeypatch):
    r=run_case(tmp_path,monkeypatch,'none',text(json.dumps({'content':'def solve(a,b): return a-b'})),index=4)
    assert r['information_complete'] is True
    assert (r['protocol_status'],r['signal'])==('VERIFIED_SUCCESS','VERIFIED_SUCCESS')
