import asyncio,hashlib,json
import pytest
from alienape.agent import Agent,Session,ToolDenied
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.diagnostics import DiagnosticStore
from alienape.experience import ExperienceStore
from alienape.intent_resolution import DeterministicIntentResolver,IntentID as I
from alienape.profiles import ModelProfile
from alienape.routed_agent import RoutedAgent
from alienape.routing import ModelRegistry
from alienape.task_slots import ContentMode as M
from alienape.tools import Workspace
from test_diagnostics import bundle,enable


@pytest.mark.parametrize('prompt',['lösche index.html','entferne index.html','delete index.html','remove index.html','bitte lösche die Datei index.html',"delete 'index.html'"])
def test_unsupported_delete_controller_only_and_trace(tmp_path,prompt):
    target=tmp_path/'index.html';target.write_text('still here',encoding='utf-8')
    settings=Settings();settings.models=[ModelProfile('demo','demo','mock','demo')]
    store=ExperienceStore(tmp_path/'experience.sqlite3');digest=hashlib.sha256(store.database.read_bytes()).hexdigest()
    agent=RoutedAgent(Workspace(tmp_path),settings,registry=ModelRegistry(settings.models),experience=store)
    diagnostic=enable(agent,tmp_path)
    def fail_dispatch(*_):pytest.fail('Unsupported delete must never dispatch a tool')
    agent.tools.execute=fail_dispatch
    answer=asyncio.run(agent.run(prompt));trace=bundle(diagnostic,tmp_path)[0][0]
    assert agent.intent_resolution.intent_id==I.DELETE_REQUEST_UNSUPPORTED
    assert agent.intent_resolution.slots.target_paths==('index.html',)
    assert 'nicht unterstützt' in answer and 'nichts verändert' in answer
    assert target.read_text(encoding='utf-8')=='still here'
    assert agent.resources.counters.model_calls==agent.resources.counters.tool_calls==agent.resources.counters.file_writes==0
    assert not agent.experience_ids and hashlib.sha256(store.database.read_bytes()).hexdigest()==digest
    assert trace['intent']['canonical_intent']=='DELETE_REQUEST_UNSUPPORTED' and trace['intent']['source']=='deterministic-v0682'
    assert trace['task_slots']['target_paths']==['index.html'] and trace['task_slots']['slot_sources']['target_path']=='RAW_LEXICAL'
    assert not trace['model_requests'] and not trace['tools']['dispatches']
    assert trace['resources']['turn_delta']['model_calls']==0 and trace['resources']['turn_delta']['file_writes']==0
    assert trace['final_response']['source']=='CONTROLLER' and not trace['experience']['produced']


def test_delete_content_request_clarifies_instead_of_file_deletion(tmp_path):
    path=tmp_path/'index.html';path.write_text('keep text',encoding='utf-8')
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(agent.run('lösche den Text aus index.html'))
    assert agent.intent_resolution.intent_id==I.MODIFY_FILE
    assert 'Inhalt' in answer and 'konkretisieren' in answer
    assert agent.resources.counters.model_calls==agent.resources.counters.file_writes==0
    assert path.read_text()=='keep text'


def test_unsafe_delete_target_is_not_resolved_or_accessed(tmp_path):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(agent.run('delete ../../outside.py'))
    assert 'nicht unterstützt' in answer and agent.intent_resolution.slots.target_paths==()
    assert agent.resources.counters.model_calls==agent.resources.counters.tool_calls==0


@pytest.mark.parametrize('prompt,target,payload',[
 ('mach eine text datei namens hallo.txt und schreib rein (ich affe allso aff ich)','hallo.txt','ich affe allso aff ich'),
 ('erstelle note.txt und schreib rein (Hallo Welt)','note.txt','Hallo Welt'),
 ('erstelle note.txt und schreibe hinein (a "quoted" value)','note.txt','a "quoted" value'),
 ('erstelle note.txt mit ( Hallo Welt )','note.txt','Hallo Welt'),
])
def test_parenthesized_literal_uses_permission_and_trace(tmp_path,prompt,target,payload):
    approvals=[]
    async def allow(name,args,path):approvals.append((name,args['path']));return True
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=allow);diag=enable(agent,tmp_path)
    asyncio.run(agent.run(prompt));trace=bundle(diag,tmp_path)[0][0]
    assert (tmp_path/target).read_text(encoding='utf-8')==payload
    assert agent.intent_resolution.slots.content_mode==M.LITERAL
    assert agent.intent_resolution.slots.literal_content==payload
    assert approvals==[('write_file',target)] and agent.resources.counters.model_calls==0
    assert trace['task_slots']['slot_sources']['target_path']=='RAW_LEXICAL'
    assert trace['task_slots']['slot_sources']['literal_content']=='PARENTHESIZED_USER_PAYLOAD'
    assert trace['task_slots']['content_mode']=='LITERAL'


@pytest.mark.parametrize('prompt',[
 'erstelle note.txt und schreib rein (Hallo',
 'erstelle note.txt und schreib rein (Hallo (Welt))',
 'erstelle note.txt und schreib rein (Hallo) und lösche x.txt',
 'erstelle note.txt und schreib rein (A) (B)',
 'erstelle note.txt mit (Hallo (Welt))',
 'erstelle note.txt mit (Hallo',
])
def test_broken_or_ambiguous_parentheses_never_write(tmp_path,prompt):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(agent.run(prompt))
    assert answer and agent.intent_resolution.ambiguity_reason
    assert not (tmp_path/'note.txt').exists()
    assert agent.resources.counters.model_calls==agent.resources.counters.file_writes==agent.resources.counters.tool_calls==0


def test_parenthesized_literal_does_not_bypass_permission(tmp_path):
    async def deny(*_):return False
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=deny)
    with pytest.raises(ToolDenied):asyncio.run(agent.run('erstelle note.txt und schreib rein (Hallo Welt)'))
    assert not (tmp_path/'note.txt').exists()


def test_normal_python_parentheses_remain_generation():
    r=DeterministicIntentResolver().resolve('erstelle python.py mit einer Funktion foo(x)')
    assert r.intent_id==I.CREATE_FILE and r.slots.content_mode==M.GENERATE
    assert r.slots.literal_content is None and 'foo(x)' in r.slots.generation_request


def test_quoted_literal_with_marker_is_not_reinterpreted(tmp_path):
    async def allow(*_):return True
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=allow)
    asyncio.run(agent.run('erstelle note.txt mit "und schreib rein (bitte)"'))
    assert (tmp_path/'note.txt').read_text()=='und schreib rein (bitte)'
    assert agent.intent_resolution.slots.slot_sources['literal_content']=='RAW_LITERAL'


def test_delete_bypasses_enabled_semantic_resolver(tmp_path):
    settings=Settings();settings.semantic_resolver.enabled=True;settings.semantic_resolver.model='configured-embedding'
    class Resolver:
        async def suggest(self,*_):pytest.fail('Deletion must never use the semantic resolver')
    path=tmp_path/'index.html';path.write_text('keep')
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),settings,semantic_resolver=Resolver())
    answer=asyncio.run(agent.run('lösche index.html'))
    assert 'nicht unterstützt' in answer and not agent.resolver_chain['semantic_used']
    assert agent.semantic_usage['semantic_resolver_calls']==agent.resources.counters.model_calls==agent.resources.counters.tool_calls==0
    assert path.read_text()=='keep'


def test_generated_code_context_cannot_be_overwritten_by_parenthesis_payload(tmp_path):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(agent.run('erstelle python.py mit einer Funktion foo(x) und schreib rein (x)'))
    assert agent.intent_resolution.slots.literal_content is None and agent.intent_resolution.ambiguity_reason
    assert answer and not (tmp_path/'python.py').exists()
    assert agent.resources.counters.model_calls==agent.resources.counters.tool_calls==0


@pytest.mark.parametrize('prompt',[
    'erstelle note.txt mit "Hallo und schreib rein (Welt)',
    'erstelle note.txt mit \'Vorher\' und schreib rein (Nachher)',
])
def test_unclosed_quote_or_competing_content_cannot_trigger_literal_write(tmp_path,prompt):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(agent.run(prompt))
    assert answer and agent.intent_resolution.ambiguity_reason
    assert not (tmp_path/'note.txt').exists()
    assert agent.resources.counters.model_calls==agent.resources.counters.tool_calls==0
