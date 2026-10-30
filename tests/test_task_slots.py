import asyncio,json,time
from dataclasses import replace
import pytest
from alienape.agent import Agent,Session
from alienape.backends import MockBackend,tool_reply
from alienape.config import Settings
from alienape.intent_resolution import DeterministicIntentResolver,IntentID as I
from alienape.task_slots import ContentMode as M
from alienape.tools import Workspace

async def yes(*args):return True

def agent(tmp_path,session=None,responses=(),permission=yes):
    return Agent(MockBackend(list(responses),delay=0),Workspace(tmp_path),Settings(),session=session,permission=permission)

@pytest.mark.parametrize('text,path,mode,content',[
 ('erstelle hello.txt mit dem Text \'Hallo Welt\'','hello.txt',M.LITERAL,'Hallo Welt'),
 ('eine text datei namens hallowelt.txt mit dem inhalt \'hallo welt\' erstellen','hallowelt.txt',M.LITERAL,'hallo welt'),
 ('erstelle helloworld.py mit einem Hello-World-Programm','helloworld.py',M.GENERATE,None),
 ('erstelle hello.py mit einem hello world programm drin','hello.py',M.GENERATE,None),
 ('erstelle eine leere Datei test.txt','test.txt',M.EMPTY,''),
 ('erstelle eine Python-Datei mit Hello World',None,M.GENERATE,None),
 ('schreib Hello World in C++ und speichere es in hello.cpp','hello.cpp',M.GENERATE,None),
 ('erstelle eine index.html Seite mit einem OK Button','index.html',M.GENERATE,None),
 ('erstelle hello.txt mit \'unterminated','hello.txt',M.UNKNOWN,None),
])
def test_independent_slots(text,path,mode,content):
    r=DeterministicIntentResolver().resolve(text);s=r.slots
    assert r.intent_id==I.CREATE_FILE
    assert s.target_paths==((path,) if path else ())
    assert s.content_mode==mode and s.literal_content==content
    if not path:assert 'target_path' in s.missing_slots and s.generation_request


def test_literal_no_worker_and_postwrite_verification(tmp_path):
    a=agent(tmp_path);asyncio.run(a.run("eine text datei namens hallowelt.txt mit dem inhalt 'hallo welt' erstellen"))
    assert (tmp_path/'hallowelt.txt').read_text()=='hallo welt'
    assert a.resources.counters.model_calls==0
    assert a.verification.result.verification_status in {'NOT_RUN'}


def test_pending_filename_completes_generate_in_new_agent(tmp_path):
    session=Session();first=agent(tmp_path,session)
    answer=asyncio.run(first.run('mach eine Python-Datei mit Hello World'))
    assert 'heißen' in answer and session.task_state.pending_task and not first.resources.counters.model_calls
    second=agent(tmp_path,session,[tool_reply('write_file',{'path':'helloworld.py','content':'print("Hello World")\n'})])
    asyncio.run(second.run('helloworld.py'))
    assert second.intent_resolution.intent_id==I.CREATE_FILE
    assert second.intent_resolution.slots.resolution_source=='SESSION_PENDING'
    assert (tmp_path/'helloworld.py').read_text()=='print("Hello World")\n'
    assert not session.task_state.pending_task and not second.required_input_paths


def test_pending_unrelated_cleared_no_mutation(tmp_path):
    session=Session();asyncio.run(agent(tmp_path,session).run('erstelle eine Python-Datei mit Hello World'))
    second=agent(tmp_path,session);asyncio.run(second.run('hallo'))
    assert session.task_state.pending_task is None and not second.resources.counters.file_writes
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('question',['was?','warum?','wieso?','bei was?','was meinst du?','what?','why?','what do you mean?'])
def test_clarification_one_turn(tmp_path,question):
    session=Session();asyncio.run(agent(tmp_path,session).run('erstelle eine Python-Datei mit Hello World'))
    second=agent(tmp_path,session);answer=asyncio.run(second.run(question))
    assert 'Datei' in answer and not second.resources.counters.model_calls
    third=agent(tmp_path,session);third_answer=asyncio.run(third.run('warum?'))
    assert 'konkretisieren' in third_answer and not third.resources.counters.model_calls


def test_last_generated_artifact_explicit_save(tmp_path):
    session=Session();first=agent(tmp_path,session,[{'role':'assistant','content':'```python\nprint("Hallo Welt")\n```'}])
    asyncio.run(first.run('schreib mir ein Hello-World-Programm in Python'))
    assert session.task_state.last_generated_artifact and not list(tmp_path.iterdir())
    approvals=[]
    async def allow(name,args,path):approvals.append(name);return True
    second=agent(tmp_path,session,permission=allow);asyncio.run(second.run('speicher das als hello.py'))
    assert (tmp_path/'hello.py').read_text()=='print("Hallo Welt")'
    assert approvals==['write_file'] and second.resources.counters.model_calls==0
    assert session.task_state.last_generated_artifact is None


def test_artifact_not_saved_without_permission(tmp_path):
    session=Session();first=agent(tmp_path,session,[{'role':'assistant','content':'```python\nprint(1)\n```'}]);asyncio.run(first.run('schreib mir Hello World in Python'))
    async def deny(*args):return False
    second=agent(tmp_path,session,permission=deny)
    with pytest.raises(Exception):asyncio.run(second.run('save it as hello.py'))
    assert not (tmp_path/'hello.py').exists()


def test_expiry_and_workspace_switch(tmp_path):
    session=Session();first=agent(tmp_path,session);asyncio.run(first.run('erstelle eine Python-Datei mit Hello World'))
    state=session.task_state;state.pending_task=replace(state.pending_task,created_at=time.monotonic()-301)
    second=agent(tmp_path,session,[{'role':'assistant','content':'unrelated'}]);asyncio.run(second.run('hallo'))
    assert not state.pending_task
    asyncio.run(agent(tmp_path,session).run('erstelle eine Python-Datei mit Hello World'))
    other=tmp_path/'other';other.mkdir();state.bind(Workspace(other))
    assert not state.pending_task and not state.last_generated_artifact


def test_valid_target_survives_bad_content_no_write(tmp_path):
    a=agent(tmp_path);answer=asyncio.run(a.run("erstelle safe.txt mit 'unterminated"))
    assert a.intent_resolution.slots.target_paths==('safe.txt',)
    assert 'Inhalt' in answer and a.resources.counters.model_calls==0
    assert not (tmp_path/'safe.txt').exists()
