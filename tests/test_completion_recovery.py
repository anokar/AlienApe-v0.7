import asyncio
import copy
import json
import threading
import httpx
import pytest
from alienape.agent import Agent
from alienape.backends import MockBackend, OllamaBackend, LMStudioBackend, tool_reply
from alienape.completion import ModelResponse, CompletionMetadata, FailureType as F, CompletionRejected
from alienape.validators import JSONValidator, JSONSchemaValidator, ExactValueValidator, ValidationResult
from alienape.config import Settings
from alienape.controller import ResourceController
from alienape.experience import ExperienceStore, ModelFingerprint, SuccessSignal as S
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry
from alienape.tools import Workspace
from test_routing import profile
from test_routed_agent import Pool
from test_gui import app, window, until

EXPECTED={'A':False,'B':True,'C':True,'D':False,'E':False}


def response(text,reason='stop',*,input_tokens=100,output_tokens=None,calls=None):
    return ModelResponse({'role':'assistant','content':text,**({'tool_calls':calls} if calls else {})},
        completion=CompletionMetadata(reason,input_tokens,output_tokens))


class Script(MockBackend):
    def __init__(self,responses,*,pause_retry=False):
        super().__init__(responses,delay=0)
        self.requests=[]
        self.pause_retry=pause_retry

    async def chat(self,messages,tools=None,model='',max_tokens=None):
        limit=getattr(self,'requested_output_limit',None)
        self.requests.append({'model':model,'messages':copy.deepcopy(messages),'tools':copy.deepcopy(tools),'limit':limit})
        if self.pause_retry and len(self.requests)>1:
            await asyncio.sleep(10)
        value=copy.deepcopy(next(self.responses))
        if isinstance(value,ModelResponse):
            value.completion.requested_output_limit=limit
            value.completion.model=model
            value.completion.provider='mock'
            value.completion.api_success=True
            value.completion.http_status=200
            if value.completion.output_tokens is None:
                value.completion.output_tokens=limit if value.finish_reason=='length' else 20
            self.last_completion=value.completion
            self.last_usage={'prompt_tokens':value.input_tokens,'completion_tokens':value.output_tokens}
        return value


def make(tmp_path,values,*,mode='native',validator=None,context_length=0,callback=None):
    s=Settings()
    s.models=[profile('small','coding','low',tool_mode=mode,context_length=context_length),
              profile('large','coding','high',tool_mode='native')]
    backend=Script(values)
    large=Script([response('LARGE MUST NOT BE CALLED')])
    events=[]
    def observe(kind,details,*rest):
        events.append((kind,details))
        if callback:
            callback(kind,details)
    store=ExperienceStore(tmp_path/'experience.db')
    agent=RoutedAgent(Workspace(tmp_path),s,registry=ModelRegistry(s.models),pool=Pool({'small':backend,'large':large}),
                      experience=store,validator=validator or ExactValueValidator(EXPECTED),observe=observe)
    return agent,backend,large,events,store


@pytest.mark.parametrize('factory',[OllamaBackend,LMStudioBackend])
@pytest.mark.parametrize('reason',['stop','length',None])
def test_production_metadata_preserves_actual_fields(factory,reason):
    def handle(request):
        body=json.loads(request.content)
        return httpx.Response(200,json={'choices':[{'finish_reason':reason,'message':{'role':'assistant','content':'{}','reasoning':'SECRET_THOUGHT'}}],
                                       'usage':{'prompt_tokens':11,'completion_tokens':1024}})
    backend=factory(transport=httpx.MockTransport(handle))
    result=asyncio.run(backend.chat_with_mode([{'role':'user','content':'JSON'}],model='test',mode='native'))
    assert isinstance(result,ModelResponse) and result=={'role':'assistant','content':'{}'}
    assert result.finish_reason==(reason or 'UNKNOWN') and result.input_tokens==11 and result.output_tokens==1024
    assert result.requested_output_limit==1024 and result.completion.limit_exactly_reached
    assert result.completion.http_status==200 and result.completion.api_success
    assert 'SECRET_THOUGHT' not in json.dumps(result.completion.to_dict()) and 'reasoning' not in result


@pytest.mark.parametrize('reason,valid,complete,expected',[
    ('length',False,False,F.RESOURCE_OUTPUT_LIMIT),('stop',True,True,F.SUCCESS),
    ('stop',False,False,F.FORMAT_FAILURE),('UNKNOWN',False,False,F.UNKNOWN),
    ('length',True,True,F.SUCCESS),('stop',None,None,F.UNKNOWN)])
def test_classifier(reason,valid,complete,expected):
    assert ResourceController().classify_completion(response('',reason),ValidationResult(valid,complete,True,F.FORMAT_FAILURE))==expected


def test_tool_calls_are_intermediate(tmp_path):
    call=tool_reply('read_file',{'path':'code.gd'})
    (tmp_path/'code.gd').write_text('known')
    tool_response=response(None,'tool_calls',calls=call['tool_calls'])
    agent,backend,large,events,store=make(tmp_path,[tool_response,response(json.dumps(EXPECTED))])
    assert asyncio.run(agent.run('Prüfe code.gd und liefere JSON'))==json.dumps(EXPECTED)
    assert len([k for k,d in events if k=='VALIDATION_RESULT'])==1
    assert agent.resources.counters.file_reads==1 and not agent.resources.counters.resource_retries


def test_real001_mock_recovery_exact_context_and_experience(tmp_path):
    (tmp_path/'code.gd').write_text('known valid input')
    tool=tool_reply('read_file',{'path':'code.gd'})
    agent,backend,large,events,store=make(tmp_path,[response(None,'tool_calls',calls=tool['tool_calls']),
        response('{"A":false,"B":true,"C":true,"D":','length'),response(json.dumps(EXPECTED),output_tokens=1031)])
    assert asyncio.run(agent.run('Analysiere nur code.gd; antworte mit JSON A-E.'))==json.dumps(EXPECTED)
    assert [r['limit'] for r in backend.requests]==[1024,1024,2048]
    assert backend.requests[1]['messages']==backend.requests[2]['messages']
    assert backend.requests[1]['tools']==backend.requests[2]['tools']
    assert {r['model'] for r in backend.requests}=={'small'} and not large.requests
    assert agent.resources.counters.file_reads==1 and agent.resources.counters.resource_retries==1
    assert (tmp_path/'code.gd').read_text()=='known valid input'
    assert len([k for k,d in events if k=='RESOURCE_RETRY_STARTED'])==1
    assert any(k=='RESOURCE_RETRY_SUCCESS' for k,d in events)
    assert not any(k=='MODEL_ESCALATED' for k,d in events)
    with store.connection() as db:
        rows=db.execute('SELECT * FROM model_experience WHERE task_category=? ORDER BY id',(str(agent.category),)).fetchall()
    assert len(rows)==2 and rows[0]['success_signal']=='RESOURCE_FAILURE'
    assert rows[0]['failure_type']=='RESOURCE_OUTPUT_LIMIT' and rows[0]['success_value'] is None
    assert rows[1]['success_signal']=='VERIFIED_SUCCESS' and rows[1]['resource_retry_success']
    assert rows[1]['required_output_budget_observed']==2048 and rows[1]['actual_output_tokens']==1031
    assert rows[0]['model_fingerprint_id']==rows[1]['model_fingerprint_id']
    summary=store.summary(rows[0]['model_fingerprint_id'],str(agent.category))
    assert summary['hard_sample_count']==1 and summary['verified_failure_count']==0
    assert summary['success_rate']==1
    assert getattr(backend,'requested_output_limit',None) is None


@pytest.mark.parametrize('text,reason,failure',[
    ('{','stop',F.FORMAT_FAILURE),('{','UNKNOWN',F.UNKNOWN),
    (json.dumps({**EXPECTED,'A':True}),'stop',F.VALIDATION_FAILURE)])
def test_no_resource_retry_for_other_failure_types(tmp_path,text,reason,failure):
    agent,backend,large,events,store=make(tmp_path,[response(text,reason)])
    with pytest.raises(CompletionRejected) as exc:
        asyncio.run(agent.run('Prüfe Godot und liefere JSON'))
    assert exc.value.failure_type==failure and len(backend.requests)==1 and not large.requests
    assert not any(k=='RESOURCE_RETRY_STARTED' for k,d in events)


def test_repeated_truncation_stops_after_one_retry(tmp_path):
    agent,backend,large,events,store=make(tmp_path,[response('{','length'),response('{','length')])
    with pytest.raises(CompletionRejected) as exc:
        asyncio.run(agent.run('Godot JSON'))
    assert exc.value.failure_type==F.RESOURCE_OUTPUT_LIMIT
    assert [r['limit'] for r in backend.requests]==[1024,2048] and not large.requests
    assert any(k=='RESOURCE_RETRY_FAILED' for k,d in events)
    with store.connection() as db:
        rows=db.execute('SELECT success_value FROM model_experience WHERE task_category=?',(str(agent.category),)).fetchall()
        assert len(rows)==2 and all(r[0] is None for r in rows)


def test_valid_length_response_is_success_without_retry(tmp_path):
    agent,backend,large,events,store=make(tmp_path,[response(json.dumps(EXPECTED),'length')])
    asyncio.run(agent.run('Godot JSON'))
    assert len(backend.requests)==1 and agent.last_failure_type==F.SUCCESS
    assert any(k=='OUTPUT_BUDGET_NEAR_LIMIT' for k,d in events)


def test_max_limit_and_disable_retry(tmp_path):
    agent,backend,large,events,store=make(tmp_path,[response('{','length'),response(json.dumps(EXPECTED))])
    agent.settings.resources.output.max_limit=1536
    asyncio.run(agent.run('Godot JSON'))
    assert [r['limit'] for r in backend.requests]==[1024,1536]
    other=tmp_path/'other'
    other.mkdir()
    agent,backend,_,events,_=make(other,[response('{','length')])
    agent.settings.resources.output.max_retries=0
    with pytest.raises(CompletionRejected):
        asyncio.run(agent.run('Godot JSON'))
    assert len(backend.requests)==1


def test_known_context_limit_blocks_increase(tmp_path):
    agent,backend,large,events,store=make(tmp_path,[response('{','length',input_tokens=1500)],context_length=3072)
    with pytest.raises(CompletionRejected) as exc:
        asyncio.run(agent.run('Godot JSON'))
    assert exc.value.failure_type==F.RESOURCE_CONTEXT_LIMIT and len(backend.requests)==1
    assert not any(k=='RESOURCE_RETRY_STARTED' for k,d in events) and not large.requests


def test_stop_before_retry(tmp_path):
    stopped=threading.Event()
    agent,backend,_,events,_=make(tmp_path,[response('{','length')],callback=lambda k,d:stopped.set() if k=='RESOURCE_OUTPUT_LIMIT' else None)
    agent.stopped=stopped
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run('Godot JSON'))
    assert len(backend.requests)==1 and not agent.resources.counters.resource_retries


def test_stop_during_retry(tmp_path):
    agent,backend,_,events,_=make(tmp_path,[response('{','length'),response(json.dumps(EXPECTED))])
    backend.pause_retry=True
    async def run():
        task=asyncio.create_task(agent.run('Godot JSON'))
        while len(backend.requests)<2:
            await asyncio.sleep(.001)
        agent.stopped.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task,.5)
    asyncio.run(run())
    assert len(backend.requests)==2 and any(k=='RESOURCE_RETRY_FAILED' for k,d in events)


def test_none_mode_uses_same_packet_for_recovery(tmp_path):
    agent,backend,_,events,_=make(tmp_path,[response('{','length'),response(json.dumps(EXPECTED))],mode='none')
    asyncio.run(agent.run('Godot JSON'))
    assert backend.requests[0]['messages']==backend.requests[1]['messages']
    assert [r['limit'] for r in backend.requests]==[1024,2048]
    assert backend.requests[0]['tools'] is None


def test_cache_change_blocks_stale_context_reuse(tmp_path):
    target=tmp_path/'code.gd'
    target.write_text('old')
    def change(kind,details):
        if kind=='RESOURCE_OUTPUT_LIMIT':
            target.write_text('new content externally changed')
    tool=tool_reply('read_file',{'path':'code.gd'})
    agent,backend,_,events,_=make(tmp_path,[response(None,'tool_calls',calls=tool['tool_calls']),response('{','length')],callback=change)
    with pytest.raises(CompletionRejected) as exc:
        asyncio.run(agent.run('Godot code.gd JSON'))
    assert exc.value.failure_type==F.INFORMATION_MISSING and len(backend.requests)==2


def test_no_validator_keeps_unverified_answer_without_recovery(tmp_path):
    backend=Script([response('ordinary unfinished prose','length')])
    agent=Agent(backend,Workspace(tmp_path),Settings())
    assert asyncio.run(agent.run('Erkläre etwas kurz'))=='ordinary unfinished prose'
    assert agent.last_validation.valid is None and not agent.last_validation.verified
    assert len(backend.requests)==1 and not agent.resources.counters.resource_retries


def test_resource_store_defends_against_capability_failure_label(tmp_path):
    store=ExperienceStore(tmp_path/'store.db')
    p=profile('test')
    fp=ModelFingerprint.for_profile(p)
    store.register(p,fp)
    identifier=store.record(fp.id,'GODOT_CODE',S.VERIFIED_FAILURE,failure_type='RESOURCE_OUTPUT_LIMIT')
    store.feedback(identifier,False)
    assert store.summary(fp.id,'GODOT_CODE')['hard_sample_count']==0


def test_output_config_round_trip_and_bounded_retry(tmp_path):
    s=Settings()
    path=tmp_path/'config.toml'
    s.save(path)
    assert Settings.load(path)==s
    s.resources.output.max_retries=2
    with pytest.raises(ValueError):
        s.validate()


def test_gui_stop_during_resource_retry(window,monkeypatch):
    backend=Script([response('{','length'),response('{}')],pause_retry=True)
    monkeypatch.setattr('alienape.ui.create_backend',lambda s:backend)
    window.input.setPlainText('Antworte mit JSON')
    window.send_button.click()
    until(lambda:len(backend.requests)==2)
    assert any(e['event_type']=='RESOURCE_RETRY_STARTED' for e in window.events)
    window.stop_button.click()
    until(lambda:window.worker is None,timeout=1)
    assert 'Gestoppt' in window.chat.toPlainText() and window.send_button.isEnabled()


def test_json_fallback_recovers_without_changing_protocol_or_prompt(tmp_path):
    bodies=[]
    def handle(request):
        body=json.loads(request.content)
        bodies.append(body)
        text='{"answer":' if len(bodies)==1 else '{"answer":"fertig"}'
        reason='length' if len(bodies)==1 else 'stop'
        return httpx.Response(200,json={'choices':[{'finish_reason':reason,'message':{'role':'assistant','content':text}}],
            'usage':{'prompt_tokens':100,'completion_tokens':body['max_tokens'] if len(bodies)==1 else 12}})
    raw=OllamaBackend(transport=httpx.MockTransport(handle))
    from alienape.routed_agent import ModeBackend
    p=profile('test',tool_mode='json_fallback')
    backend=ModeBackend(raw,p)
    events=[]
    agent=Agent(backend,Workspace(tmp_path),Settings(),observe=lambda k,d,*a:events.append((k,d)))
    assert asyncio.run(agent.run('Erkläre kurz etwas'))=='fertig'
    assert [b['max_tokens'] for b in bodies]==[1024,2048]
    assert {k:v for k,v in bodies[0].items() if k!='max_tokens'}=={k:v for k,v in bodies[1].items() if k!='max_tokens'}
    assert bodies[0]['response_format']==bodies[1]['response_format']
    assert any(k=='RESOURCE_RETRY_SUCCESS' for k,d in events)


def test_stop_on_retry_started_prevents_second_request(tmp_path):
    stopped=threading.Event()
    agent,backend,_,events,_=make(tmp_path,[response('{','length')],callback=lambda k,d:stopped.set() if k=='RESOURCE_RETRY_STARTED' else None)
    agent.stopped=stopped
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run('Godot JSON'))
    assert len(backend.requests)==1


def test_retry_respects_global_model_call_budget(tmp_path):
    agent,backend,large,events,_=make(tmp_path,[response('{','length')])
    agent.model_call_limit=1
    with pytest.raises(CompletionRejected):
        asyncio.run(agent.run('Godot JSON'))
    assert len(backend.requests)==1 and not large.requests


def test_json_schema_missing_required_key_is_incomplete():
    validator=JSONSchemaValidator({'type':'object','properties':{k:{'type':'boolean'} for k in EXPECTED},
                                    'required':list(EXPECTED),'additionalProperties':False})
    result=validator.validate('',response('{"A":false}'))
    assert result.valid is False and result.complete is False and result.failure_type==F.VALIDATION_FAILURE
    assert validator.validate('',response(json.dumps(EXPECTED))).valid


def test_native_completion_reason_does_not_invent_stop():
    meta=CompletionMetadata.from_api({'done':True,'eval_count':3},{'max_tokens':64,'model':'x'},provider='ollama',elapsed_ms=1,http_status=200)
    assert meta.finish_reason=='UNKNOWN' and meta.output_tokens==3


def test_timeout_during_retry_is_not_capability_failure(tmp_path):
    class TimeoutScript(Script):
        async def chat(self,*args,**kwargs):
            if self.requests:
                raise httpx.ReadTimeout('test')
            return await super().chat(*args,**kwargs)
    agent,_,large,events,store=make(tmp_path,[])
    backend=TimeoutScript([response('{','length')])
    agent.pool.models['small']=backend
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(agent.run('Godot JSON'))
    assert not large.requests and agent.last_failure_type==F.TIMEOUT
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM model_experience WHERE failure_type='MODEL_CAPABILITY_FAILURE'").fetchone()
