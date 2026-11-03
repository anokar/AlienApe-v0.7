import asyncio,json
import pytest
from alienape.agent import Agent
from alienape.backends import MockBackend, tool_reply
from alienape.completion import ModelResponse, CompletionMetadata
from alienape.config import Settings
from alienape.experience import ExperienceStore
from alienape.profiles import ModelProfile
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry
from alienape.tools import Workspace
from alienape.project_orchestrator import execute_project,summarize_project
from alienape.project_plan import InvalidProjectPlan


def plan(*paths):
    return json.dumps({'files':[{'path':p,'purpose':'generate','language':'python','depends_on':list(deps)} for p,deps in paths]})


def test_orchestration_orders_and_limits_scopes(tmp_path):
    async def allow(*_):return True
    events=[]
    backend=MockBackend([tool_reply('write_file',{'path':'project/src/app.py','content':'def add(a,b): return a+b\n'}),tool_reply('write_file',{'path':'project/tests/test_app.py','content':'def test_add(): assert 1+1==2\n'})],delay=0)
    agent=Agent(backend,Workspace(tmp_path),Settings(),permission=allow,observe=lambda k,d,*_:events.append((k,d)))
    result=asyncio.run(execute_project(agent,plan(('tests/test_app.py',('src/app.py',)),('src/app.py',())),'project','small python app'))
    assert result.project_status=='SUCCESS'
    assert [x.write_status for x in result.files]==['SUCCESS','SUCCESS']
    assert (tmp_path/'project/src/app.py').is_file() and (tmp_path/'project/tests/test_app.py').is_file()
    assert all(f.target in summarize_project(result) for f in result.files)
    requests=[d['name'] for k,d in events if k=='TOOL_REQUEST']
    assert requests.index('make_directory') < requests.index('write_file')


def test_invalid_plan_and_existing_root_are_noops(tmp_path):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    with pytest.raises(InvalidProjectPlan):asyncio.run(execute_project(agent,plan(('../bad.py',())),'project','x'))
    assert not list(tmp_path.iterdir())
    (tmp_path/'project').mkdir(); (tmp_path/'project/existing').write_text('keep')
    with pytest.raises(FileExistsError):asyncio.run(execute_project(agent,plan(('x.py',())),'project','x'))
    assert (tmp_path/'project/existing').read_text()=='keep'


def test_permission_denial_skips_dependent_file(tmp_path):
    async def deny(*_):return False
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=deny)
    result=asyncio.run(execute_project(agent,plan(('a.py',()),('b.py',('a.py',))),'project','x'))
    assert [x.write_status for x in result.files]==['SKIPPED_DEPENDENCY','SKIPPED_DEPENDENCY']
    assert not (tmp_path/'project').exists()


def test_dependency_context_is_scoped(tmp_path):
    async def allow(*_):return True
    backend=MockBackend([tool_reply('write_file',{'path':'project/src/app.py','content':'def add(a,b): return a+b\n'}),tool_reply('write_file',{'path':'project/tests/test_app.py','content':'def test_add(): assert 1+1==2\n'})],delay=0)
    agent=Agent(backend,Workspace(tmp_path),Settings(),permission=allow)
    result=asyncio.run(execute_project(agent,plan(('src/app.py',()),('tests/test_app.py',('src/app.py',))),'project','small python app'))
    assert result.project_status=='SUCCESS'
    second=json.dumps(backend.calls[1],ensure_ascii=False)
    assert 'def add(a,b)' in second
    assert agent.write_scope.allowed_paths==['project/tests/test_app.py']
    assert not agent.write_scope.permits('project/src/app.py',agent.workspace)


def test_invalid_file_response_skips_dependent(tmp_path):
    class NoTools(MockBackend):
        def get_capabilities(self):return {'tool_calling':False,'structured_output':False,'context_length':None}
    async def allow(*_):return True
    agent=Agent(NoTools([{'role':'assistant','content':'```python\nprint(1)\n```'}],delay=0),Workspace(tmp_path),Settings(),permission=allow)
    result=asyncio.run(execute_project(agent,plan(('a.py',()),('b.py',('a.py',))),'project','x'))
    assert [x.write_status for x in result.files]==['FAILURE','SKIPPED_DEPENDENCY']
    assert not (tmp_path/'project/a.py').exists()
    assert not (tmp_path/'project/b.py').exists()
    assert len(agent.backend.calls)==1


def test_atomic_write_oserror_is_controller_failure_and_skips_dependents(tmp_path, monkeypatch):
    class NoToolMock(MockBackend):
        def get_capabilities(self):
            return {'tool_calling':False,'structured_output':False,'context_length':None}

    class Pool:
        def __init__(self, backend):self.backend=backend
        def get(self, _profile):return self.backend

    async def allow(*_):return True
    events=[]
    settings=Settings()
    settings.models=[ModelProfile('coder','Coder','mock','mock-demo',role='coding',capabilities=['coding'],tool_mode='none')]
    settings.routing.default_model=settings.routing.fallback_model='coder'
    backend=NoToolMock([{'role':'assistant','content':json.dumps({'content':'value = 1\n'})}],delay=0)
    store=ExperienceStore(tmp_path/'experience.db')
    workspace=tmp_path/'workspace';workspace.mkdir()
    agent=RoutedAgent(Workspace(workspace),settings,registry=ModelRegistry(settings.models),pool=Pool(backend),
        manual_model='coder',permission=allow,experience=store,observe=lambda k,d,*_:events.append((k,d)))
    target=tmp_path/'workspace/project/a.py'
    real_replace=__import__('os').replace
    replace_attempts=[]
    def fail_target_replace(source,destination):
        if __import__('pathlib').Path(destination)==target:
            replace_attempts.append((source,destination))
            raise OSError('injected atomic replace failure')
        return real_replace(source,destination)
    monkeypatch.setattr('alienape.tools.os.replace',fail_target_replace)

    result=asyncio.run(execute_project(agent,plan(('a.py',()),('b.py',('a.py',))),'project','create two Python files'))

    assert agent.file_content_protocol_status=='VERIFIED_SUCCESS'
    assert replace_attempts
    assert result.files[0].write_status=='FAILURE'
    assert result.files[0].failure_attribution=='TOOL_FAILURE'
    assert any(k=='FAILURE_CLASSIFIED' and d.get('failure_type')=='TOOL_FAILURE' and d.get('subreason')=='WRITE_IO_FAILURE' for k,d in events)
    assert result.files[1].write_status=='SKIPPED_DEPENDENCY'
    assert result.project_status=='FAILURE'
    assert not target.exists()
    assert not (tmp_path/'workspace/project/b.py').exists()
    assert not list(target.parent.glob('.alienape-*'))
    assert not any(k=='VERIFICATION_COMPLETED' for k,_ in events)
    with store.connection() as db:
        rows=db.execute('SELECT failure_type,success_signal,success_value,protocol_status,response_contract FROM model_experience').fetchall()
    assert len(rows)==1
    assert tuple(rows[0])==('TOOL_FAILURE','UNKNOWN',None,'VERIFIED_SUCCESS','FILE_CONTENT')
    assert len(backend.calls)==1


def test_verification_failure_after_successful_write_fails_project_and_skips_dependents(tmp_path):
    class NoToolMock(MockBackend):
        def get_capabilities(self):
            return {'tool_calling':False,'structured_output':False,'context_length':None}

    class Pool:
        def __init__(self, backend):self.backend=backend
        def get(self, _profile):return self.backend

    async def allow(*_):return True
    events=[]
    settings=Settings()
    settings.models=[ModelProfile('coder','Coder','mock','mock-demo',role='coding',capabilities=['coding'],tool_mode='none')]
    settings.routing.default_model=settings.routing.fallback_model='coder'
    backend=NoToolMock([{'role':'assistant','content':json.dumps({'content':'def broken(:\n'})}],delay=0)
    store=ExperienceStore(tmp_path/'experience.db')
    workspace=tmp_path/'workspace';workspace.mkdir()
    agent=RoutedAgent(Workspace(workspace),settings,registry=ModelRegistry(settings.models),pool=Pool(backend),
        manual_model='coder',permission=allow,experience=store,observe=lambda k,d,*_:events.append((k,d)))

    result=asyncio.run(execute_project(agent,plan(('a.py',()),('b.py',('a.py',))),'project','create two Python files'))

    assert agent.file_content_protocol_status=='VERIFIED_SUCCESS'
    assert agent.written_paths==['project/a.py']
    assert (workspace/'project/a.py').read_text(encoding='utf-8')=='def broken(:\n'
    assert result.files[0].write_status=='SUCCESS'
    assert result.files[0].verification_status=='FAIL'
    assert result.files[0].failure_attribution=='VALIDATION_FAILURE'
    assert result.files[1].write_status=='SKIPPED_DEPENDENCY'
    assert result.project_status=='FAILURE'
    completed=[d for k,d in events if k=='VERIFICATION_COMPLETED']
    assert len(completed)==1 and completed[0]['verification_status']=='FAIL'
    assert any(k=='PROJECT_FILE_STEP_RESULT' and d['write_status']=='SUCCESS' and d['verification_status']=='FAIL' and d['attribution']=='VALIDATION_FAILURE' for k,d in events)
    with store.connection() as db:
        rows=db.execute('SELECT failure_type,success_signal,success_value,protocol_status,response_contract,verification_json FROM model_experience').fetchall()
    assert len(rows)==1
    row=rows[0]
    assert row['failure_type']=='VALIDATION_FAILURE'
    assert row['success_signal']=='VERIFIED_FAILURE' and row['success_value']==0
    assert row['protocol_status']=='VERIFIED_SUCCESS' and row['response_contract']=='FILE_CONTENT'
    assert json.loads(row['verification_json'])['verification_status']=='FAIL'
    assert len(backend.calls)==1


def test_project_builder_output_limit_retries_once_then_fails_neutrally(tmp_path):
    class TruncatedBackend(MockBackend):
        def __init__(self, responses):
            super().__init__(responses,delay=0)
            self.requests=[]

        def get_capabilities(self):
            return {'tool_calling':False,'structured_output':False,'context_length':None}

        async def chat(self,messages,tools=None,model='',max_tokens=None):
            limit=getattr(self,'requested_output_limit',None)
            self.requests.append({'limit':limit,'model':model,'messages':messages,'tools':tools})
            response=next(self.responses)
            response.completion.requested_output_limit=limit
            response.completion.output_tokens=limit
            response.completion.model=model
            response.completion.provider='mock'
            response.completion.api_success=True
            response.completion.http_status=200
            self.last_completion=response.completion
            self.last_usage={}
            return response

    class Pool:
        def __init__(self, backend):self.backend=backend
        def get(self, _profile):return self.backend

    async def allow(*_):return True
    events=[]
    settings=Settings()
    settings.models=[ModelProfile('coder','Coder','mock','mock-demo',role='coding',capabilities=['coding'],tool_mode='none')]
    settings.routing.default_model=settings.routing.fallback_model='coder'
    backend=TruncatedBackend([
        ModelResponse({'role':'assistant','content':'{"content":"def build('},completion=CompletionMetadata('length')),
        ModelResponse({'role':'assistant','content':'{"content":"def build(project):\n    return project'},completion=CompletionMetadata('length')),
    ])
    store=ExperienceStore(tmp_path/'experience.db')
    workspace=tmp_path/'workspace';workspace.mkdir()
    agent=RoutedAgent(Workspace(workspace),settings,registry=ModelRegistry(settings.models),pool=Pool(backend),
        manual_model='coder',permission=allow,experience=store,observe=lambda k,d,*_:events.append((k,d)))

    result=asyncio.run(execute_project(agent,plan(('a.py',()),('b.py',('a.py',))),'project','create two Python files'))

    assert [request['limit'] for request in backend.requests]==[1024,2048]
    assert [request['model'] for request in backend.requests]==['mock-demo','mock-demo']
    assert agent.resources.counters.resource_retries==1
    assert getattr(backend,'requested_output_limit',None) is None
    assert agent.last_failure_type=='RESOURCE_OUTPUT_LIMIT'
    assert agent.file_content_protocol_status is None
    assert len(agent.validation_history)==2
    assert all(item['failure_type']=='RESOURCE_OUTPUT_LIMIT' for item in agent.validation_history)
    assert result.files[0].write_status=='FAILURE'
    assert result.files[0].failure_attribution=='RESOURCE_OUTPUT_LIMIT'
    assert result.files[1].write_status=='SKIPPED_DEPENDENCY'
    assert result.project_status=='FAILURE'
    assert not (workspace/'project/a.py').exists()
    assert not (workspace/'project/b.py').exists()
    assert not list((workspace/'project').glob('.alienape-*'))
    assert not any(k=='FILE_WRITE' for k,_ in events)
    assert not any(k=='VERIFICATION_COMPLETED' for k,_ in events)
    assert len([1 for k,_ in events if k=='RESOURCE_OUTPUT_LIMIT'])==2
    assert len([1 for k,_ in events if k=='RESOURCE_RETRY_STARTED'])==1
    with store.connection() as db:
        rows=db.execute('''SELECT failure_type,success_signal,success_value,protocol_status,response_contract,
            resource_retry_used,resource_retry_success,requested_output_limit,actual_output_tokens,finish_reason
            FROM model_experience ORDER BY id''').fetchall()
    assert len(rows)==2
    assert all(row['failure_type']=='RESOURCE_OUTPUT_LIMIT' and row['success_signal']=='RESOURCE_FAILURE' for row in rows)
    assert all(row['success_value'] is None and row['protocol_status'] is None and row['response_contract']=='FILE_CONTENT' for row in rows)
    assert [(row['requested_output_limit'],row['actual_output_tokens'],row['finish_reason']) for row in rows]==[(1024,1024,'length'),(2048,2048,'length')]
    assert [row['resource_retry_used'] for row in rows]==[0,1]
    assert all(row['resource_retry_success'] in (0,None) for row in rows)
