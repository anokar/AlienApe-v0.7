import asyncio,json,hashlib
from zipfile import ZipFile
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.memory import MemoryStore
from alienape.tools import Workspace
from alienape.diagnostics import DiagnosticStore

class NoToolsMock(MockBackend):
    def get_capabilities(self):return {'tool_calling':False,'structured_output':False,'context_length':None}

CANARY='FAKE_KEY_TEST_ONLY_91A8B7C3'

def test_sensitive_memory_rejected_and_export_redacted(tmp_path):
    store=MemoryStore(tmp_path/'memory.db','project');store.put('manual','FACT','manueller Eintrag','user')
    settings=Settings();settings.diagnostic_trace=True
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings,memory=store)
    diag=DiagnosticStore(tmp_path/'traces');a.diagnostic_store=diag
    answer=asyncio.run(a.run('merk dir: api_key='+CANARY))
    assert CANARY not in answer and 'nicht' in answer and not a.resources.counters.memory_writes and not a.resources.counters.model_calls
    with store.connection() as db:assert [tuple(row) for row in db.execute('select logical_key,content from memory')]==[('manual','manueller Eintrag')]
    path=tmp_path/'bundle.zip';diag.export(path)
    with ZipFile(path) as z:
        assert set(z.namelist())=={'CHAT.md','TRACE.jsonl','SUMMARY.json','MANIFEST.json'}
        for name in z.namelist():assert CANARY.encode() not in z.read(name)
        manifest=json.loads(z.read('MANIFEST.json'))
        for name,h in manifest['file_sha256'].items():assert hashlib.sha256(z.read(name)).hexdigest()==h
        assert b'<REDACTED>' in z.read('CHAT.md')
    for p in (tmp_path/'traces').rglob('*'):
        if p.is_file():assert CANARY.encode() not in p.read_bytes()

def test_unlabelled_known_key_and_normal_memory(tmp_path):
    store=MemoryStore(tmp_path/'memory.db','project');a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
    value='sk-'+('A9b8' * 8)
    answer=asyncio.run(a.run('merk dir: '+value));assert 'nicht' in answer and value not in answer
    answer=asyncio.run(a.run('merk dir: affen testen affen'));assert 'Gemerkt' in answer
    with store.connection() as db:assert db.execute('select count(*) from memory').fetchone()[0]==1
import pytest
from alienape.intent_resolution import DeterministicIntentResolver,IntentID
from alienape.task_slots import ContentMode
from alienape.agent import Session
from alienape.completion import CompletionRejected
from test_workspace_discovery import make
from test_completion_recovery import response

def test_content_contract_write_and_alternate_path(tmp_path):
    code='#include <stdio.h>\nint main(){return 0;}\n'
    a,b=make(tmp_path,responses=[response(json.dumps({'content':code}))])
    async def allow(*_):return True
    a.permission=allow
    answer=asyncio.run(a.run('erstelle hello.cpp mit einem Hello World Programm'))
    assert a.response_contract.mode=='FILE_CONTENT' and (tmp_path/'hello.cpp').read_bytes()==code.encode()
    assert code in answer and not (tmp_path/'wrong.cpp').exists()
    c,d=make(tmp_path,responses=[response(json.dumps({'content':code,'path':'wrong.cpp'}))]);c.permission=allow
    with pytest.raises(CompletionRejected):asyncio.run(c.run('erstelle second.cpp mit einem Hello World Programm'))
    assert not (tmp_path/'second.cpp').exists() and not (tmp_path/'wrong.cpp').exists()

def test_generation_language_slots():
    r=DeterministicIntentResolver()
    for text in ['make a hello world program in zig','erstelle eine kleine cpp datei']:
        slots=r.resolve(text).slots
        assert slots.intent_id==IntentID.CREATE_FILE and slots.content_mode==ContentMode.GENERATE and slots.missing_slots==('target_path',)

def test_pending_filename_before_semantic(tmp_path):
    settings=Settings();settings.semantic_resolver.enabled=True;settings.semantic_resolver.model='test'
    class FakeResolver:
        async def suggest(self,*_):pytest.fail('pending slot must win before semantic')
    session=Session();a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings,session=session,semantic_resolver=FakeResolver())
    answer=asyncio.run(a.run('make a hello world program in zig'));assert session.task_state.pending_task
    b=Agent(NoToolsMock([{'role':'assistant','content':json.dumps({'content':'pub fn main() void {}'})}],delay=0),Workspace(tmp_path),settings,session=session,semantic_resolver=FakeResolver())
    async def allow(*_):return True
    b.permission=allow;asyncio.run(b.run('hello.zig'))
    assert b.intent_resolution.intent_id==IntentID.CREATE_FILE and (tmp_path/'hello.zig').exists()

def test_directory_missing_and_status(tmp_path):
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(a.run('analysiere missing-project/'))
    assert 'nicht gefunden' in answer and a.resources.counters.model_calls==0
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    answer=asyncio.run(b.run('welches model benutze ich?'))
    assert 'Routing: AUTO' in answer and 'Letzter Worker:' in answer and b.resources.counters.model_calls==0

def test_directory_existing_is_bounded_to_target(tmp_path):
    folder=tmp_path/'src';folder.mkdir();(folder/'main.py').write_text('print(1)');(tmp_path/'other.py').write_text('print(2)')
    a=Agent(MockBackend([{'role':'assistant','content':'Quelle analysiert.'}],delay=0),Workspace(tmp_path),Settings())
    asyncio.run(a.run('analysiere src/'))
    assert a.workspace_discovery and [x['path'] for x in a.workspace_discovery.files]==['src/main.py']
    assert a.workspace_discovery.limits['directories']==4
    with pytest.raises(PermissionError):asyncio.run(Agent(MockBackend(delay=0),Workspace(tmp_path),Settings()).run('analysiere ../outside/'))

def test_content_standalone_displays_canonical_bytes(tmp_path):
    content='int main() { return 0; }\n';backend=NoToolsMock([{'role':'assistant','content':json.dumps({'content':content})}],delay=0)
    async def allow(*_):return True
    a=Agent(backend,Workspace(tmp_path),Settings(),permission=allow)
    result=asyncio.run(a.run('erstelle hello.cpp mit einem Hello World Programm'))
    assert content in result and (tmp_path/'hello.cpp').read_bytes()==content.encode()
    assert a.response_contract.mode=='FILE_CONTENT'

def test_status_manual_and_semantic_separated(tmp_path):
    from alienape.profiles import ModelProfile
    from alienape.routing import ModelRegistry
    from alienape.routed_agent import RoutedAgent
    settings=Settings();settings.models=[ModelProfile('worker','worker','mock','worker-model')]
    settings.semantic_resolver.enabled=True;settings.semantic_resolver.model='embedding-model'
    agent=RoutedAgent(Workspace(tmp_path),settings,registry=ModelRegistry(settings.models),manual_model='worker')
    answer=asyncio.run(agent.run('welches model benutze ich gerade?'))
    assert 'MANUELL' in answer and 'worker-model' in answer and 'Semantic Resolver: embedding-model' in answer
    assert not agent.resources.counters.model_calls

def test_trace_every_event_and_model_metadata_redacts_canary(tmp_path):
    from alienape.security import redactor
    token='SYNTHETIC_TEST_TOKEN_38E79B5D';redactor.register(token)
    settings=Settings();settings.diagnostic_trace=True
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings)
    diag=DiagnosticStore(tmp_path/'traces');trace=diag.begin(a,'user '+token)
    trace.record('EVENT',{'kind':'CONTROLLER_ACTION','details':{'message':token}})
    trace.record('MODEL_REQUEST',{'messages':[{'role':'user','content':'request '+token}],'metadata':{'model':token}})
    trace.record('FINAL',{'text':'response '+token,'source':'CONTROLLER'})
    path=tmp_path/'bundle.zip';diag.export(path)
    with ZipFile(path) as z:
        for name in z.namelist():assert token.encode() not in z.read(name)
        assert b'<REDACTED>' in z.read('TRACE.jsonl') and b'<REDACTED>' in z.read('CHAT.md')
    for p in (tmp_path/'traces').rglob('*'):
        if p.is_file():assert token.encode() not in p.read_bytes()

def test_multi_target_remains_file_proposal(tmp_path):
    from alienape.contracts import ResponseMode
    r=DeterministicIntentResolver().resolve('create a.py and b.py with generated code')
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());a.intent_resolution=r;a.resolution_task='create a.py and b.py with generated code'
    a.prepare_task(a.resolution_task)
    assert len(a.write_scope.allowed_paths)==2 and a.response_contract.mode==ResponseMode.FILE_PROPOSAL

def test_pending_unrelated_turn_clears(tmp_path):
    session=Session();first=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
    asyncio.run(first.run('make a hello world program in zig'));assert session.task_state.pending_task
    second=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
    asyncio.run(second.run('hallo'));assert session.task_state.pending_task is None

def test_known_target_display_and_hash_events(tmp_path):
    code='print("Hello")\n';events=[]
    backend=NoToolsMock([{'role':'assistant','content':json.dumps({'content':code})}],delay=0)
    async def allow(*_):return True
    a=Agent(backend,Workspace(tmp_path),Settings(),permission=allow,observe=lambda k,d,*_:events.append((k,d)))
    answer=asyncio.run(a.run('erstelle hello.py mit einem Hello World Programm'))
    event=next(d for k,d in events if k=='PROPOSAL_DISPLAY_HASH')
    h=hashlib.sha256((tmp_path/'hello.py').read_bytes()).hexdigest()
    assert event['canonical_parsed_content_sha256']==event['written_bytes_sha256']==event['displayed_accepted_content_sha256']==h
    assert code in answer

def test_quoted_secret_redacts_later_metadata(tmp_path):
    from alienape.security import redactor
    secret='SYNTHETIC VALUE 91C8D3'
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    a.settings.diagnostic_trace=True;a.diagnostic_store=DiagnosticStore(tmp_path/'traces')
    answer=asyncio.run(a.run('merk dir: password="'+secret+'"'))
    assert secret not in answer
    trace=a.diagnostic_store.begin(a,'later text '+secret);trace.record('FINAL',{'text':secret,'source':'CONTROLLER'})
    archive=tmp_path/'out.zip';a.diagnostic_store.export(archive)
    with ZipFile(archive) as z:
        for name in z.namelist():assert secret.encode() not in z.read(name)

def test_model_routing_selection_without_call_not_last_worker(tmp_path):
    from alienape.profiles import ModelProfile
    from alienape.routing import ModelRegistry
    from alienape.routed_agent import RoutedAgent
    p=ModelProfile('worker','worker','mock','worker-model');s=Settings();s.models=[p]
    a=RoutedAgent(Workspace(tmp_path),s,registry=ModelRegistry([p]))
    a.emit('MODEL_ROUTED',{'to_model':'worker'})
    assert not a.session.task_state.last_worker_model

def test_pending_choice_before_deterministic_resolver(tmp_path):
    (tmp_path/'index.html').write_text('keep');session=Session()
    first=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
    asyncio.run(first.run('lösche den Text aus index.html'))
    class NeverResolve:
        def resolve(self,*_):pytest.fail('pending choice must be resolved before general classification')
    second=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session,intent_resolver=NeverResolve())
    answer=asyncio.run(second.run('ganze datei'))
    assert 'nicht unterstützt' in answer and (tmp_path/'index.html').read_text()=='keep'
