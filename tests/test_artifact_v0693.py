import asyncio
from alienape.agent import Agent,Session
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.tools import Workspace

def test_c_artifact_followup(tmp_path):
    session=Session();code='int sum(void) { return 3 + 8; }'
    a=Agent(MockBackend([{'role':'assistant','content':'```c\n'+code+'\n```'}],delay=0),Workspace(tmp_path),Settings(),session=session)
    asyncio.run(a.run('schreib mir eine C funktion die 3+8 rechnet'))
    assert session.task_state.last_generated_artifact and session.task_state.last_generated_artifact.content==code
    async def allow(*_):return True
    b=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),session=session,permission=allow)
    asyncio.run(b.run('speichere die funktion in calc.c'))
    assert (tmp_path/'calc.c').read_text()==code and b.resources.counters.model_calls==0

def test_prose_not_captured(tmp_path):
    session=Session();a=Agent(MockBackend([{'role':'assistant','content':'Eine Funktion könnte diese Zahlen addieren.'}],delay=0),Workspace(tmp_path),Settings(),session=session)
    asyncio.run(a.run('schreib mir eine C funktion die 3+8 rechnet'))
    assert session.task_state.last_generated_artifact is None
