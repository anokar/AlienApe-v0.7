import asyncio,pytest
from alienape.agent import Agent,Session
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.memory import MemoryStore
from alienape.tools import Workspace

@pytest.mark.parametrize('task,payload',[("remember that my preferred output format is markdown","my preferred output format is markdown"),("merk dir für dieses projekt: projektname ist banana-engine","projektname ist banana-engine"),("merk dir mein lieblingswort ist banane","mein lieblingswort ist banane")])
def test_explicit_persistence_restart_and_relevance(tmp_path,task,payload):
 store=MemoryStore(tmp_path/'memory.sqlite3','project');a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
 result=asyncio.run(a.run(task));rows=MemoryStore(tmp_path/'memory.sqlite3','project').all()
 assert len(rows)==1 and rows[0]['content']==payload and a.resources.counters.model_calls==0
 assert MemoryStore(tmp_path/'memory.sqlite3','other').all()==[]

def test_forget_single_and_ambiguous_no_files(tmp_path):
 store=MemoryStore(tmp_path/'m.sqlite3','project');store.put('word-a','FACT','mein lieblingswort ist banane');store.put('word-b','FACT','mein lieblingswort ist mango')
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store);file=tmp_path/'banana.txt';file.write_text('safe')
 result=asyncio.run(a.run('vergiss lieblingswort'));assert 'nichts gelöscht' in result and len(store.all())==2
 result=asyncio.run(a.run('vergiss banane'));assert 'wurde gelöscht' in result and len(store.all())==1 and file.read_text()=='safe'

def test_memory_off_no_deletion(tmp_path):
 store=MemoryStore(tmp_path/'m.sqlite3','project');store.put('a','FACT','safe');settings=Settings();settings.memory.enabled=False
 a=Agent(MockBackend(delay=0),Workspace(tmp_path),settings,memory=store)
 assert 'ausgeschaltet' in asyncio.run(a.run('vergiss safe')) and len(store.all())==1


def test_project_memory_question_is_deterministic(tmp_path):
 store=MemoryStore(tmp_path/'m.sqlite3','project');store.put('project','FACT','projektname ist banana-engine')
 agent=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
 answer=asyncio.run(agent.run('was weißt du über mein projekt?'))
 assert 'banana-engine' in answer and agent.resources.counters.model_calls==0
