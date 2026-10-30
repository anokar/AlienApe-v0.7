import asyncio
from alienape.agent import Agent,Session
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.tools import Workspace

def test_pending_explicit_literal(tmp_path):
    s=Session();a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=s)
    asyncio.run(a.run('schreibe eine lol.txt'))
    assert s.task_state.pending_task
    async def yes(*_):return True
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=s,permission=yes)
    asyncio.run(b.run('ausdrücklicher text "hallo"'))
    assert (tmp_path/'lol.txt').read_text()=='hallo' and b.resources.counters.model_calls==0

def test_modify_pending_slot(tmp_path):
    (tmp_path/'hello.py').write_text('old');s=Session()
    a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=s);asyncio.run(a.run('lösche den inhalt von hello.py'))
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=s);result=asyncio.run(b.run('den inhalt'))
    assert 'hello.py' in result and s.task_state.pending_task.slots.missing_slots==('change_description',)
    assert (tmp_path/'hello.py').read_text()=='old'

def test_pending_explicit_generation(tmp_path):
    import json
    s=Session();a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=s)
    asyncio.run(a.run('schreibe eine lol.txt'));assert s.task_state.pending_task
    class NoTools(MockBackend):
        def get_capabilities(self):return {'tool_calling':False,'structured_output':False,'context_length':None}
    async def yes(*_):return True
    b=Agent(NoTools([{'role':'assistant','content':json.dumps({'content':'Hello!'})}],delay=0),Workspace(tmp_path),Settings(),session=s,permission=yes)
    asyncio.run(b.run('generiere: einen kurzen gruß'))
    assert (tmp_path/'lol.txt').read_text()=='Hello!' and b.intent_resolution.slots.generation_request=='einen kurzen gruß'
