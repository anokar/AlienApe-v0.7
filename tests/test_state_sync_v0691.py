import asyncio,hashlib,json,pytest
from alienape.agent import Agent,Session
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.intent_resolution import DeterministicIntentResolver,IntentID as I
from alienape.task_slots import ContentMode
from alienape.tools import Workspace
from alienape.completion import CompletionRejected
from test_workspace_discovery import make
from test_completion_recovery import response

def test_code_response_and_file_intents():
    resolver=DeterministicIntentResolver()
    a=resolver.resolve('schreib mir eine C funktion die 3+8 rechnet')
    b=resolver.resolve('schreib die Funktion in calc.c')
    assert a.intent_id==I.CODE_GENERATION_RESPONSE and not a.mutation_requested and not a.referenced_paths
    assert b.intent_id==I.CREATE_FILE and b.slots.content_mode==ContentMode.GENERATE and b.output_paths==('calc.c',)

def test_directory_and_behavior(tmp_path):
    resolver=DeterministicIntentResolver()
    a=resolver.resolve('mach ein ordner namens hallo');b=resolver.resolve('mach eine python datei die einen ordner öffnet')
    assert a.intent_id==I.CREATE_DIRECTORY and a.output_paths==('hallo',)
    assert b.intent_id==I.CREATE_FILE and b.slots.content_mode==ContentMode.GENERATE
    async def allow(*_):return True
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),permission=allow)
    asyncio.run(agent.run('mach ein ordner namens hallo'))
    assert (tmp_path/'hallo').is_dir() and agent.resources.counters.model_calls==0

def test_capability_global_and_languages(tmp_path):
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    result=asyncio.run(a.run('was für programmiersprachen kannst du?'))
    assert 'Globale Fähigkeit' in result and 'Dateien und Ordner' in result and 'Modell' in result and 'Shell' in result
    assert a.resources.counters.model_calls==0

def test_memory_absence_does_not_claim_success(tmp_path):
    a=Agent(MockBackend([{'role':'assistant','content':'Ich habe es gespeichert'}],delay=0),Workspace(tmp_path),Settings())
    result=asyncio.run(a.run('merk dir: affen testen affen'))
    assert 'ausgeschaltet' in result and a.resources.counters.memory_writes==0 and not a.resources.counters.model_calls

@pytest.mark.parametrize('choice,intent',[('den inhalt',I.MODIFY_FILE),('ganze datei',I.DELETE_REQUEST_UNSUPPORTED)])
def test_pending_choice(tmp_path,choice,intent):
    (tmp_path/'index.html').write_text('old');session=Session()
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
    assert 'konkretisieren' in asyncio.run(a.run('lösche den Text aus index.html'))
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session)
    result=asyncio.run(b.run(choice))
    assert b.intent_resolution.intent_id==intent and b.intent_resolution.referenced_paths==('index.html',)
    assert b.resources.counters.model_calls==b.resources.counters.file_writes==0 and (tmp_path/'index.html').read_text()=='old'
    assert ('nicht unterstützt' if intent==I.DELETE_REQUEST_UNSUPPORTED else 'Welche Änderung') in result

def test_pending_choice_unrelated_clears(tmp_path):
    (tmp_path/'index.html').write_text('old');session=Session()
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session);asyncio.run(a.run('lösche den Text aus index.html'))
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session);asyncio.run(b.run('hallo'))
    assert session.task_state.pending_choice is None

def test_proposal_display_matches_written(tmp_path):
    content='int main() { return 0; }\n'
    value={'content':content}
    a,b=make(tmp_path,responses=[response(json.dumps(value))]);events=[]
    a.observe=lambda k,d,*_:events.append((k,d))
    async def allow(*_):return True
    a.permission=allow
    result=asyncio.run(a.run('erstelle main.cpp mit einem generierten C++ programm'))
    h=hashlib.sha256((tmp_path/'main.cpp').read_bytes()).hexdigest()
    event=next(d for k,d in events if k=='PROPOSAL_DISPLAY_HASH')
    assert event['canonical_parsed_content_sha256']==event['written_bytes_sha256']==event['displayed_accepted_content_sha256']==h
    assert content in result and 'NOT_RUN' in result

def test_disagreeing_proposal_cannot_write(tmp_path):
    value={'payload':'```cpp\nint main(){return 0;}\n```','status':'ok','proposed_files':[{'path':'main.cpp','content':'int main(){return 0;<}\n'},{'path':'other.cpp','content':'int x;'}]}
    a,b=make(tmp_path,responses=[response(json.dumps(value))]);asyncio.run
    async def allow(*_):return True
    a.permission=allow
    with pytest.raises(CompletionRejected):asyncio.run(a.run('create main.cpp and other.cpp with generated code'))
    assert not (tmp_path/'main.cpp').exists() and not (tmp_path/'other.cpp').exists() and a.resources.counters.file_writes==0

def test_existing_memory_unchanged_if_persistence_unavailable(tmp_path):
    from alienape.memory import MemoryStore
    store=MemoryStore(tmp_path/'memory.sqlite3','project')
    store.put('existing','FACT','manually kept','user')
    settings=Settings();settings.memory.enabled=False
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),settings,memory=store)
    answer=asyncio.run(agent.run('merk dir: affen testen affen'))
    assert 'ausgeschaltet' in answer and agent.resources.counters.memory_writes==0
    with store.connection() as db:
        rows=db.execute('SELECT logical_key,content,source FROM memory').fetchall()
    assert [tuple(row) for row in rows]==[('existing','manually kept','user')]

def test_duplicate_proposal_keys_rejected(tmp_path):
    text='{"payload":"ok","status":"ok","proposed_files":[{"path":"main.cpp","content":"good","content":"bad"}]}'
    a,b=make(tmp_path,responses=[response(text)])
    async def allow(*_):return True
    a.permission=allow
    with pytest.raises(CompletionRejected):asyncio.run(a.run('erstelle main.cpp mit einem generierten C++ programm'))
    assert not (tmp_path/'main.cpp').exists() and a.resources.counters.file_writes==0

def test_choice_cleared_on_workspace_change(tmp_path):
    one=tmp_path/'one';two=tmp_path/'two';one.mkdir();two.mkdir();(one/'index.html').write_text('old')
    session=Session();a=Agent(MockBackend(delay=0),Workspace(one),Settings(),session=session)
    asyncio.run(a.run('lösche den Text aus index.html'));assert session.task_state.pending_choice
    b=Agent(MockBackend([{'role':'assistant','content':'Bitte präzisieren.'}],delay=0),Workspace(two),Settings(),session=session)
    asyncio.run(b.run('den inhalt'))
    assert session.task_state.pending_choice is None and not b.resources.counters.file_writes

def test_worker_memory_claim_without_controller_write_is_rejected(tmp_path):
    agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings())
    agent.turn_model_calls_before=0;agent.turn_memory_writes_before=0
    agent.resources.counters.model_calls=1
    result=agent.finish('Erkläre mir etwas','Ich habe das im Memory gespeichert.')
    assert 'keine dauerhafte Memory-Speicherung bestätigt' in result
