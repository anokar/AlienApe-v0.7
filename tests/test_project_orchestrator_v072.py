import asyncio,json
import pytest
from alienape.agent import Agent
from alienape.backends import MockBackend, tool_reply
from alienape.config import Settings
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
