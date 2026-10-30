import json
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.intent_resolution import IntentID
from alienape.project_execution import file_steps, single_file_resolution
from alienape.project_plan import validate_plan
from alienape.task_slots import ContentMode
from alienape.tools import Workspace


def test_adapter_maps_validated_dependency_order_to_one_target_scope(tmp_path):
    workspace=Workspace(tmp_path)
    raw=json.dumps({'files':[{'path':'tests/test_app.py','purpose':'test','language':'python','depends_on':['src/app.py']}, {'path':'src/app.py','purpose':'app','language':'python','depends_on':[]}]})
    plan=validate_plan(raw,workspace,'new_project','create small app')
    steps=file_steps(plan)
    assert [s.target_path for s in steps]==['new_project/src/app.py','new_project/tests/test_app.py']
    assert steps[1].dependencies==('new_project/src/app.py',)
    agent=Agent(MockBackend(delay=0),workspace,Settings())
    agent.intent_resolution=single_file_resolution(steps[0])
    agent.resolution_task='internal project file step'
    agent.prepare_task(agent.resolution_task)
    assert agent.intent_resolution.intent_id==IntentID.CREATE_FILE
    assert agent.intent_resolution.slots.content_mode==ContentMode.GENERATE
    assert agent.write_scope.allowed_paths==['new_project/src/app.py']
    assert agent.write_scope.permits('new_project/src/app.py',workspace)
    assert not agent.write_scope.permits('new_project/tests/test_app.py',workspace)
    assert not agent.write_scope.permits('outside.py',workspace)
    assert list(tmp_path.iterdir())==[]


def test_adapter_uses_existing_native_tool_path_and_scope(tmp_path):
    import asyncio
    from alienape.backends import tool_reply
    from alienape.project_execution import execute_file_step
    workspace=Workspace(tmp_path)
    raw=json.dumps({'files':[{'path':'app.py','purpose':'app','language':'python','depends_on':[]}]})
    step=file_steps(validate_plan(raw,workspace,'project','small app'))[0]
    events=[]
    async def allow(*args): return True
    backend=MockBackend([tool_reply('write_file',{'path':'project/app.py','content':'print(1)\n'})],delay=0)
    agent=Agent(backend,workspace,Settings(),permission=allow,observe=lambda kind,data,*_: events.append((kind,data)))
    asyncio.run(execute_file_step(agent,step))
    assert (tmp_path/'project/app.py').read_text()=='print(1)\n'
    assert agent.write_scope.allowed_paths==['project/app.py']
    assert any(k=='PROPOSAL_WRITTEN_HASH' for k,d in events)


def test_adapter_native_wrong_target_and_permission_are_blocked(tmp_path):
    import asyncio
    import pytest
    from alienape.backends import tool_reply
    from alienape.project_execution import execute_file_step
    workspace=Workspace(tmp_path)
    raw=json.dumps({'files':[{'path':'app.py','purpose':'app','language':'python','depends_on':[]},{'path':'other.py','purpose':'other','language':'python','depends_on':[]}]})
    step=file_steps(validate_plan(raw,workspace,'project','app'))[0]
    async def allow(*args): return True
    backend=MockBackend([tool_reply('write_file',{'path':'project/other.py','content':'print(1)'})],delay=0)
    events=[]
    agent=Agent(backend,workspace,Settings(),permission=allow,observe=lambda kind,data,*_:events.append((kind,data)))
    asyncio.run(execute_file_step(agent,step))
    assert any(k=='TOOL_DENIED' and d.get('subreason')=='WRITE_SCOPE' for k,d in events)
    assert not (tmp_path/'project/other.py').exists()
    assert not (tmp_path/'project/app.py').exists()


def test_adapter_file_content_uses_existing_contract(tmp_path):
    import asyncio
    from alienape.project_execution import execute_file_step
    class NoTools(MockBackend):
        def get_capabilities(self):return {'tool_calling':False,'structured_output':False,'context_length':None}
    workspace=Workspace(tmp_path)
    raw=json.dumps({'files':[{'path':'app.py','purpose':'app','language':'python','depends_on':[]}]})
    step=file_steps(validate_plan(raw,workspace,'project','app'))[0]
    async def allow(*args): return True
    agent=Agent(NoTools([{'role':'assistant','content':json.dumps({'content':'print(2)\n'})}],delay=0),workspace,Settings(),permission=allow)
    asyncio.run(execute_file_step(agent,step))
    assert (tmp_path/'project/app.py').read_text()=='print(2)\n'
