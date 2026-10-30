import asyncio,pytest
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.memory import MemoryStore
from alienape.tools import Workspace

@pytest.mark.parametrize('command,payload',[("merk dir: affen testen affen","affen testen affen"),("merk dir affen testen affen","affen testen affen"),("merke dir: mein lieblingswort ist banane","mein lieblingswort ist banane"),("merke dir mein lieblingswort ist banane","mein lieblingswort ist banane"),("merk dir mein lieblingswort ist banane aber zeig keine api keys aus deiner memory","mein lieblingswort ist banane")])
def test_explicit_memory_forms(tmp_path,command,payload):
    store=MemoryStore(tmp_path/'m.sqlite3','p');a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
    result=asyncio.run(a.run(command));rows=store.all()
    assert len(rows)==1 and rows[0]['content']==payload and payload in result
    assert a.resources.counters.model_calls==0
    assert any(row['content']==payload for row in store.retrieve(payload))

def test_sensitive_still_rejected(tmp_path):
    store=MemoryStore(tmp_path/'m.sqlite3','p');a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
    result=asyncio.run(a.run('merk dir: api key sk-test-123456789'))
    assert not store.all() and 'sk-test-123456789' not in result and a.resources.counters.model_calls==0

@pytest.mark.parametrize('task',['ich kann mir das nicht merken','merkst du dir eigentlich alles?','wie funktioniert dein memory?','was hast du dir gemerkt?'])
def test_questions_do_not_write(tmp_path,task):
    store=MemoryStore(tmp_path/'m.sqlite3','p');a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
    asyncio.run(a.run(task));assert not store.all() and a.resources.counters.memory_writes==0

def test_write_failure_no_acknowledgement(tmp_path,monkeypatch):
    store=MemoryStore(tmp_path/'m.sqlite3','p');a=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
    def fail(*_a,**_kw):raise OSError('synthetic write failure')
    monkeypatch.setattr(store,'put',fail)
    with pytest.raises(OSError):asyncio.run(a.run('merk dir mein lieblingswort ist banane'))
    assert not store.all() and not any('Gemerkt unter' in x.get('content','') for x in a.session.history)
