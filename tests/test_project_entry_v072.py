import asyncio
import pytest
from alienape.agent import Agent,Session
from alienape.backends import MockBackend,tool_reply
from alienape.config import Settings
from alienape.intent_resolution import IntentID,DeterministicIntentResolver
from alienape.tools import Workspace


async def allow(*_):return True


def backend():
    return MockBackend([tool_reply('write_file',{'path':'calculator/src/calculator.py','content':'def add(a,b): return a+b\n'}),tool_reply('write_file',{'path':'calculator/tests/test_calculator.py','content':'def test_add(): assert 1+1==2\n'})],delay=0)


def test_normal_entry_creates_project_and_truthful_summary(tmp_path):
    agent=Agent(backend(),Workspace(tmp_path),Settings(),permission=allow)
    answer=asyncio.run(agent.run('mach mir ein kleines python projekt namens calculator das zwei zahlen addiert und einen test hat'))
    assert agent.intent_resolution.intent_id==IntentID.PROJECT_BUILD
    assert agent.project_build_result.project_status=='SUCCESS'
    assert (tmp_path/'calculator/src/calculator.py').is_file()
    assert (tmp_path/'calculator/tests/test_calculator.py').is_file()
    assert 'calculator/src/calculator.py' in answer


def test_pending_project_name_continues_original_goal(tmp_path):
    agent=Agent(backend(),Workspace(tmp_path),Settings(),permission=allow)
    first=asyncio.run(agent.run('mach mir ein kleines python projekt das zwei zahlen addiert und einen test hat'))
    assert agent.session.pending_project and not list(tmp_path.iterdir())
    answer=asyncio.run(agent.run('calculator'))
    assert agent.intent_resolution.intent_id==IntentID.PROJECT_BUILD
    assert agent.project_build_result.project_status=='SUCCESS'
    assert (tmp_path/'calculator/tests/test_calculator.py').is_file()


def test_single_file_stays_single_and_no_project(tmp_path):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=allow)
    asyncio.run(agent.run("erstelle hello.py mit print('hello')"))
    assert agent.intent_resolution.intent_id==IntentID.CREATE_FILE
    assert not hasattr(agent,'project_build_result')


@pytest.mark.parametrize('root',['../outside','C:\\outside','foo/bar','bad?name','bad?'])
def test_unsafe_root_never_writes(tmp_path,root):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=allow)
    asyncio.run(agent.run('mach mir ein kleines python projekt namens '+root+' das zwei zahlen addiert und einen test hat'))
    assert list(tmp_path.iterdir())==[]


def test_negation_not_project():
    assert DeterministicIntentResolver().resolve('erstelle kein projekt').intent_id!=IntentID.PROJECT_BUILD


@pytest.mark.parametrize('raw',['not json','{"files": [{"path":"../outside.py","purpose":"bad","language":"python","depends_on":[]}]}','{"files": [], "shell": "bad"}'])
def test_invalid_plan_through_chat_never_writes(tmp_path,monkeypatch,raw):
    from alienape import project_entry
    monkeypatch.setattr(project_entry,'deterministic_plan',lambda _:raw)
    events=[]
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=allow,observe=lambda k,d,*_:events.append((k,d)))
    asyncio.run(agent.run('mach ein python projekt namens calculator das zwei zahlen addiert und einen test hat'))
    assert list(tmp_path.iterdir())==[]
    assert agent.resources.counters.tool_calls==0
    assert any(k=='PROJECT_PLAN_REJECTED' for k,d in events)


def test_existing_project_protected_through_chat(tmp_path):
    (tmp_path/'calculator').mkdir()
    (tmp_path/'calculator/keep.txt').write_text('keep')
    agent=Agent(backend(),Workspace(tmp_path),Settings(),permission=allow)
    asyncio.run(agent.run('mach ein python projekt namens calculator das zwei zahlen addiert und einen test hat'))
    assert (tmp_path/'calculator/keep.txt').read_text()=='keep'
    assert agent.resources.counters.model_calls==0


def test_failure_skips_dependent_through_chat(tmp_path):
    class NoTools(MockBackend):
        def get_capabilities(self):return {'tool_calling':False,'structured_output':False,'context_length':None}
    agent=Agent(NoTools([{'role':'assistant','content':'```python\nprint(1)\n```'}],delay=0),Workspace(tmp_path),Settings(),permission=allow)
    answer=asyncio.run(agent.run('mach ein python projekt namens calculator das zwei zahlen addiert und einen test hat'))
    assert [x.write_status for x in agent.project_build_result.files]==['FAILURE','SKIPPED_DEPENDENCY']
    assert 'SKIPPED_DEPENDENCY' in answer
    assert not (tmp_path/'calculator/src/calculator.py').exists()
    assert not (tmp_path/'calculator/tests/test_calculator.py').exists()
