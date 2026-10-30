import asyncio,json
from dataclasses import asdict
import pytest
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.memory import MemoryStore
from alienape.profile_v2 import ProfileV2
from alienape.profiles import ModelProfile
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry
from alienape.tools import Workspace


class Recording(MockBackend):
    def __init__(self,replies):
        super().__init__(replies,delay=0);self.tools_seen=[]
    async def chat(self,messages,tools=None,model=''):
        self.tools_seen.append(tools)
        return await super().chat(messages,tools,model)


class Pool:
    def __init__(self,backend):self.backend=backend
    def get(self,profile):return self.backend


def make(tmp_path,replies,memory=None):
    v=ProfileV2();v.task_capabilities.update(reasoning='SUPPORTED',coding='SUPPORTED',code_generation='SUPPORTED',general_chat='SUPPORTED')
    v.protocol_capabilities.update(structured_json='SUPPORTED',plain_text='SUPPORTED')
    p=ModelProfile('planner','Planner','mock','planner',role='reasoning',capabilities=['planning','coding'],tool_mode='none',profile_v2=asdict(v))
    settings=Settings();settings.models=[p];settings.verification.mode='OFF'
    async def allow(*_):return True
    events=[];backend=Recording(replies)
    a=RoutedAgent(Workspace(tmp_path),settings,registry=ModelRegistry([p]),pool=Pool(backend),memory=memory,permission=allow,observe=lambda k,d,*_:events.append((k,d)))
    return a,backend,events


def test_model_planner_routes_and_no_tools_before_mutation(tmp_path):
    plan={'files':[{'path':'notes.py','purpose':'note program','language':'python','depends_on':[]}]}
    a,b,e=make(tmp_path,[{'role':'assistant','content':json.dumps(plan)},{'role':'assistant','content':json.dumps({'content':'print("notes")\n'})}])
    result=asyncio.run(a.run('mach mir ein kleines python projekt namens notes_tool mit einem notizprogramm und tests'))
    assert b.tools_seen[0]==[]
    assert any(k=='PROJECT_PLAN_VALIDATED' and d['source']=='MODEL' for k,d in e)
    assert (tmp_path/'notes_tool/notes.py').read_text()=='print("notes")\n'
    assert a.project_build_result.plan_source=='MODEL'


@pytest.mark.parametrize('response',[{'role':'assistant','content':'```json\n{}\n```'},{'role':'assistant','content':json.dumps({'files':[{'path':'../escape','purpose':'x','language':'python','depends_on':[]}]})},{'role':'assistant','content':'{}','tool_calls':[{'id':'x'}]}])
def test_bad_planner_cannot_mutate(tmp_path,response):
    a,b,e=make(tmp_path,[response])
    asyncio.run(a.run('mach ein python projekt namens notes_tool mit notizprogramm und tests'))
    assert b.tools_seen==[[]]
    assert list(tmp_path.iterdir())==[]
    assert any(k=='PROJECT_PLAN_REJECTED' and d['source']=='MODEL' for k,d in e)


def test_planner_memory_relevance_and_secret_isolation(tmp_path):
    data=tmp_path/'data';data.mkdir();work=tmp_path/'work';work.mkdir()
    store=MemoryStore(data/'memory.db','p');store.put('notes style','PROJECT_RULE','notes uses simple python','test');store.put('lieblingswort','FACT','banane','test')
    a,b,e=make(work,[{'role':'assistant','content':'{}'}],memory=store)
    asyncio.run(a.run('mach ein python projekt namens notes_tool mit notes programm und tests'))
    packet=json.dumps(b.calls[0],ensure_ascii=False)
    assert 'notes uses simple python' in packet and 'banane' not in packet


@pytest.mark.parametrize('files',[
    [{'path':'/absolute.py','purpose':'x','language':'python','depends_on':[]}],
    [{'path':'D:/absolute.py','purpose':'x','language':'python','depends_on':[]}],
    [{'path':'foo/..\\\\escape.py','purpose':'x','language':'python','depends_on':[]}],
    [{'path':'bad?.py','purpose':'x','language':'python','depends_on':[]}],
    [{'path':'a.py','purpose':'x','language':'python','depends_on':[]},{'path':'A.py','purpose':'x','language':'python','depends_on':[]}],
    [{'path':'src/a.py','purpose':'x','language':'python','depends_on':[]},{'path':'Src/b.py','purpose':'x','language':'python','depends_on':[]}],
    [{'path':'a.py','purpose':'x','language':'python','depends_on':[],'action':'delete'}],
])
def test_security_matrix_through_application(tmp_path,files):
    a,b,e=make(tmp_path,[{'role':'assistant','content':json.dumps({'files':files})}])
    asyncio.run(a.run('mach ein python projekt namens notes_tool mit notes programm und tests'))
    assert b.tools_seen==[[]]
    assert list(tmp_path.iterdir())==[]
    assert a.resources.counters.tool_calls==0
    assert any(k=='PROJECT_PLAN_REJECTED' for k,d in e)


def test_event_order_for_model_planning(tmp_path):
    raw=json.dumps({'files':[{'path':'x.py','purpose':'x','language':'python','depends_on':[]}]})
    a,b,e=make(tmp_path,[{'role':'assistant','content':raw},{'role':'assistant','content':json.dumps({'content':'print(1)'})}])
    asyncio.run(a.run('mach ein python projekt namens notes_tool mit notes programm und tests'))
    kinds=[k for k,d in e]
    expected=['PROJECT_BUILD_REQUEST','PROJECT_ROOT_RESOLVED','PROJECT_PLAN_REQUEST','PROJECT_PLAN_RECEIVED','PROJECT_PLAN_VALIDATED','PROJECT_EXECUTION_START','PROJECT_FILE_STEP_START','PROJECT_FILE_STEP_RESULT','PROJECT_BUILD_COMPLETE']
    assert [k for k in kinds if k in expected]==expected


def test_no_eligible_planner_is_routing_not_model_failure(tmp_path):
    from alienape.agent import Agent
    events=[]
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),observe=lambda k,d,*_:events.append((k,d)))
    asyncio.run(a.run('mach ein python projekt namens notes_tool mit notes programm und tests'))
    assert a.resources.counters.model_calls==0
    assert list(tmp_path.iterdir())==[]
    assert any(k=='PROJECT_PLAN_REJECTED' and d.get('source')=='ROUTING' and d.get('attribution')=='CAPABILITY_UNAVAILABLE' for k,d in events)
