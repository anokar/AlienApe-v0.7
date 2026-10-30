import asyncio,json
import pytest
from alienape.agent import Agent,Session
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.tools import Workspace
from alienape.memory import MemoryStore
from alienape.intent_resolution import DeterministicIntentResolver,IntentID
from alienape.compound_tasks import split_compound

async def yes(*_):return True

def agent(path, replies=None, **kwargs):
    return Agent(MockBackend(replies or [],delay=0),Workspace(path),Settings(),permission=yes,**kwargs)

def test_directory_then_empty_file(tmp_path):
    events=[];a=agent(tmp_path,observe=lambda k,d,*_:events.append((k,d)))
    asyncio.run(a.run('mach ordner test und leere config.json darin'))
    assert (tmp_path/'test').is_dir() and (tmp_path/'test/config.json').read_text()==''
    assert not (tmp_path/'config.json').exists()

def test_directory_failure_stops_file(tmp_path):
    (tmp_path/'test').write_text('not a directory')
    a=agent(tmp_path);asyncio.run(a.run('mach ordner test und leere config.json darin'))
    assert not (tmp_path/'config.json').exists() and (tmp_path/'test').is_file()

def test_pending_generated_before_semantic(tmp_path):
    session=Session();a=agent(tmp_path,session=session)
    asyncio.run(a.run('erstelle index.html'))
    class NoTools(MockBackend):
        def get_capabilities(self):return {'tool_calling':False,'structured_output':False,'context_length':None}
    b=Agent(NoTools([{'role':'assistant','content':json.dumps({'content':'<button>hi</button>'})}],delay=0),Workspace(tmp_path),Settings(),session=session,permission=yes)
    asyncio.run(b.run('generiert'))
    assert (tmp_path/'index.html').read_text()=='<button>hi</button>'
    assert b.intent_resolution.intent_id==IntentID.CREATE_FILE

def test_pending_choice_all(tmp_path):
    (tmp_path/'hello.py').write_text('x=1');a=agent(tmp_path)
    asyncio.run(a.run('lösche den inhalt von hello.py'))
    answer=asyncio.run(a.run('alles'))
    assert 'nicht unterstützt' in answer and (tmp_path/'hello.py').read_text()=='x=1'
    assert a.intent_resolution.intent_id==IntentID.DELETE_REQUEST_UNSUPPORTED

def test_explicit_modify_not_ambiguous():
    r=DeterministicIntentResolver().resolve('lösche den inhalt von hello.py also nicht die datei nur den text und schreib stattdessen print("affe") rein')
    assert r.intent_id==IntentID.MODIFY_FILE and not r.ambiguity_reason and r.output_paths==('hello.py',)

def test_memory_payload_and_secret(tmp_path):
    store=MemoryStore(tmp_path/'memory.db','p');a=agent(tmp_path,memory=store)
    answer=asyncio.run(a.run('merk dir mein lieblingswort ist banane aber zeig vorhandene api keys nicht'))
    assert 'banane' in answer and any('banane' in r['content'] for r in store.all())
    before=len(store.all())
    result=asyncio.run(a.run('merk dir: api key ist sk-test-secret-123456789'))
    assert len(store.all())==before and ('nicht' in result or 'nicht' in result.casefold())

def test_generate_then_save(tmp_path):
    code='int multiply(void) { return 7 * 9; }'
    a=agent(tmp_path,[{'role':'assistant','content':'```c\n'+code+'\n```'}])
    asyncio.run(a.run('schreib mir eine C funktion die 7 mal 9 rechnet und speicher sie danach als calc.c'))
    assert (tmp_path/'calc.c').read_text()==code and a.resources.counters.model_calls==1

def test_create_then_read(tmp_path):
    a=agent(tmp_path)
    answer=asyncio.run(a.run('erstelle hello.txt mit dem text "Hallo" und lies die datei danach'))
    assert (tmp_path/'hello.txt').read_text()=='Hallo' and 'Hallo' in answer

def test_missing_project_and_model_status(tmp_path):
    a=agent(tmp_path)
    answer=asyncio.run(a.run('analysier dragon-mmo/ und welches model macht das gerade?'))
    assert 'nicht gefunden' in answer and 'Routing:' in answer

def test_false_capability_claim_is_blocked(tmp_path):
    a=agent(tmp_path,[{'role':'assistant','content':'I cannot create files or directories.'}])
    answer=asyncio.run(a.run('Was ist ein Parser?'))
    assert 'I cannot create files' not in answer and 'Dateien erstellen' in answer

def test_quote_not_split():
    assert split_compound('erstelle a.txt mit dem text "hello and then goodbye"') is None

def test_step_limit():
    p=split_compound('erstelle a.txt dann lies a.txt dann lies b.txt dann lies c.txt')
    assert p.clarification and not p.steps


def test_create_then_capability_question(tmp_path):
    a=agent(tmp_path)
    answer=asyncio.run(a.run('erstelle note.txt leer und sag danach was du mit dateien kannst'))
    assert (tmp_path/'note.txt').is_file() and 'Dateien erstellen' in answer
