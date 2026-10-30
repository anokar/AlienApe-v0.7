import asyncio,hashlib,json
from zipfile import ZipFile
import httpx,pytest
from alienape.agent import Agent
from alienape.backends import MockBackend,OllamaBackend,tool_reply
from alienape.config import Settings
from alienape.diagnostics import DiagnosticStore
from alienape.security import redactor,register_provider_secret
from alienape.tools import Workspace
from alienape.completion import CompletionRejected
from test_workspace_discovery import make
from test_completion_recovery import response


def test_diagnostic_registration_not_transport_mutation(tmp_path):
    canary='SYNTHETIC_DIAGNOSTIC_CANARY_3F9A41';redactor.register(canary)
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());store=DiagnosticStore(tmp_path/'traces')
    trace=store.begin(a,'example '+canary);trace.record('FINAL',{'text':canary,'source':'CONTROLLER'})
    bundle=tmp_path/'diag.zip';store.export(bundle)
    with ZipFile(bundle) as z:
        assert all(canary.encode() not in z.read(name) for name in z.namelist())
    received=[]
    def handle(request):
        received.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'ok'}}]})
    backend=OllamaBackend(transport=httpx.MockTransport(handle))
    token=trace.activate()
    try:asyncio.run(backend.chat([{'role':'user','content':'allowed '+canary}],model='test'))
    finally:trace.deactivate(token)
    assert received[0]['messages'][0]['content']=='allowed '+canary
    trace_bytes=b''.join(p.read_bytes() for p in (tmp_path/'traces').rglob('*.jsonl'))
    assert canary.encode() not in trace_bytes

def test_real_provider_secret_registration_remains_protected(tmp_path):
    secret='SYNTHETIC_PROVIDER_SECRET_4A7C22';register_provider_secret(secret)
    received=[]
    def handle(request):
        received.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'ok'}}]})
    asyncio.run(OllamaBackend(transport=httpx.MockTransport(handle)).chat([{'role':'user','content':'token '+secret}],model='test'))
    assert secret not in json.dumps(received)

def test_none_content_permission_hash_and_normal_budget(tmp_path):
    code='print("hello")\n';events=[]
    a,b=make(tmp_path,responses=[response(json.dumps({'content':code}))],events=events)
    a.resources.needs_tools=lambda task:False
    approvals=[]
    async def yes(name,args,path):approvals.append((name,args['path']));return True
    a.permission=yes
    answer=asyncio.run(a.run('erstelle hello.py mit einem Hello World Programm'))
    assert a.response_contract.mode=='FILE_CONTENT' and approvals==[('write_file','hello.py')]
    assert b.requests[0]['limit']==a.settings.resources.output.default_limit and b.requests[0]['limit']>128
    assert b.requests[0]['tools'] is None
    h=hashlib.sha256((tmp_path/'hello.py').read_bytes()).hexdigest()
    evidence=next(d for k,d in events if k=='PROPOSAL_DISPLAY_HASH')
    assert evidence['canonical_parsed_content_sha256']==evidence['written_bytes_sha256']==evidence['displayed_accepted_content_sha256']==h and code in answer

def test_native_exact_scope_and_permission(tmp_path):
    events=[];a,b=make(tmp_path,'native',[tool_reply('write_file',{'path':'hello.py','content':'print(1)\n'})],events=events)
    approved=[]
    async def yes(name,args,path):approved.append((name,args['path']));return True
    a.permission=yes
    asyncio.run(a.run('erstelle hello.py mit einem Hello World Programm'))
    tool=next(t for t in b.requests[0]['tools'] if t['function']['name']=='write_file')
    assert tool['function']['parameters']['properties']['path']['enum']==['hello.py']
    assert approved==[('write_file','hello.py')] and (tmp_path/'hello.py').read_text()=='print(1)\n'

def test_native_wrong_path_and_denied(tmp_path):
    a,b=make(tmp_path,'native',[tool_reply('write_file',{'path':'evil.py','content':'bad'}),response('done')])
    approval=[]
    async def yes(name,args,path):approval.append(path.name);return True
    a.permission=yes
    try:asyncio.run(a.run('erstelle hello.py mit einem Hello World Programm'))
    except (CompletionRejected,ValueError,StopIteration,RuntimeError):pass
    assert not (tmp_path/'evil.py').exists() and a.resources.counters.file_writes==0 and 'evil.py' not in approval
    c,d=make(tmp_path,'native',[tool_reply('write_file',{'path':'hello.py','content':'print(1)'})])
    async def no(*_):return False
    c.permission=no;answer=asyncio.run(c.run('erstelle hello.py mit einem Hello World Programm'))
    assert 'abgelehnt' in answer and not (tmp_path/'hello.py').exists() and c.resources.counters.file_writes==0

def test_content_fingerprint_uses_normal_output_budget(tmp_path):
    from alienape.experience import ExperienceStore
    store=ExperienceStore(tmp_path/'exp.sqlite3');a,b=make(tmp_path,responses=[response(json.dumps({'content':'print(1)'}))],store=store)
    a.resources.needs_tools=lambda task:False
    async def yes(*_):return True
    a.permission=yes;asyncio.run(a.run('erstelle hello.py mit einem Hello World Programm'))
    with store.connection() as db:
        identity=json.loads(db.execute('select identity_json from model_fingerprints').fetchone()[0])
    assert identity['max_output_tokens']==a.settings.resources.output.default_limit and b.requests[0]['limit']==identity['max_output_tokens']

def test_diagnostic_registration_does_not_mutate_pending_worker_task(tmp_path):
    from alienape.agent import Session
    value='DIAGNOSTIC_ONLY_NON_SECRET_9B4A8C';redactor.register(value)
    class NoneBackend(MockBackend):
        def get_capabilities(self):return {'tool_calling':False,'structured_output':False,'context_length':None}
    s=Session();settings=Settings();settings.diagnostic_trace=True;store=DiagnosticStore(tmp_path/'trace')
    first=Agent(NoneBackend(delay=0),Workspace(tmp_path),settings,session=s);first.diagnostic_store=store
    asyncio.run(first.run('erstelle eine Python-Datei mit einem generierten Beispiel '+value))
    assert s.task_state.pending_task and value in s.task_state.pending_task.slots.generation_request
    backend=NoneBackend([{'role':'assistant','content':json.dumps({'content':'print(1)'})}],delay=0)
    async def yes(*_):return True
    second=Agent(backend,Workspace(tmp_path),settings,session=s,permission=yes);second.diagnostic_store=store
    asyncio.run(second.run('hello.py'))
    assert value in json.dumps(backend.calls) and (tmp_path/'hello.py').is_file()
    bundle=tmp_path/'diag.zip';store.export(bundle)
    with ZipFile(bundle) as z:assert all(value.encode() not in z.read(name) for name in z.namelist())

def test_diagnostic_registration_does_not_mutate_safe_file_worker_context(tmp_path):
    value='DIAGNOSTIC_ONLY_FILE_VALUE_62B14';redactor.register(value)
    (tmp_path/'note.txt').write_text('ordinary data '+value,encoding='utf-8')
    a,b=make(tmp_path,responses=[response('Auswertung')]);a.settings.diagnostic_trace=True
    store=DiagnosticStore(tmp_path/'traces');a.diagnostic_store=store
    asyncio.run(a.run('analysiere note.txt'))
    assert value in json.dumps(b.requests)
    bundle=tmp_path/'file-diag.zip';store.export(bundle)
    with ZipFile(bundle) as z:assert all(value.encode() not in z.read(name) for name in z.namelist())

def test_actual_provider_secret_redacted_in_safe_file_context(tmp_path):
    value='SYNTHETIC_REAL_PROVIDER_VALUE_16D8';register_provider_secret(value)
    (tmp_path/'note.txt').write_text('ordinary data '+value,encoding='utf-8')
    a,b=make(tmp_path,responses=[response('Auswertung')])
    asyncio.run(a.run('analysiere note.txt'))
    assert value not in json.dumps(b.requests)

def test_diagnostic_registration_does_not_mutate_search_results(tmp_path):
    value='SAFE_DIAGNOSTIC_SEARCH_VALUE_87AB';redactor.register(value)
    (tmp_path/'note.txt').write_text('ordinary '+value,encoding='utf-8')
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings());a.active_tool_names={'search_files'}
    result=asyncio.run(a.execute_tool('search_files',{'directory':'.','query':'ordinary'}))
    assert value in json.dumps(result)
    secret='REAL_PROVIDER_SECRET_SEARCH_612B';register_provider_secret(secret)
    (tmp_path/'note2.txt').write_text('ordinary '+secret,encoding='utf-8')
    result=asyncio.run(a.execute_tool('search_files',{'directory':'.','query':'ordinary'}))
    assert secret not in json.dumps(result)

def test_diagnostic_registration_does_not_mutate_memory_retrieval(tmp_path):
    from alienape.memory import MemoryStore
    value='SAFE_DIAGNOSTIC_MEMORY_VALUE_4C95'
    store=MemoryStore(tmp_path/'memory.db','project');store.put('settings','FACT','ordinary '+value)
    redactor.register(value)
    rows=store.retrieve('settings')
    assert rows and value in rows[0]['content']
    with store.connection() as db:
        assert value in db.execute('select content from memory').fetchone()[0]
    secret='REAL_PROVIDER_SECRET_MEMORY_8A0E';register_provider_secret(secret)
    with pytest.raises(ValueError):store.put('unsafe','FACT',secret)
