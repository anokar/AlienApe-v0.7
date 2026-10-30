import sqlite3
from alienape.memory import MemoryStore

def test_existing_sensitive_memory_is_never_retrieved_raw(tmp_path):
    store=MemoryStore(tmp_path/'m.db','p');store.put('engine','FACT','Godot 4')
    secret='SYNTHETIC_OLD_9C51_AA'
    with sqlite3.connect(store.database) as db:
        db.execute("INSERT INTO memory(project_id,logical_key,type,content,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",('p','api_key','FACT',secret,'manual','2020','2020'))
        before=db.execute('select * from memory where logical_key=?',('api_key',)).fetchone()
    for rows in (store.all(),store.overview()[0],store.retrieve('api_key')):
        assert secret not in str(rows) and any(r['sensitivity']=='SENSITIVE' for r in rows)
    assert store.retrieve('engine')[0]['content']=='Godot 4'
    with sqlite3.connect(store.database) as db:assert db.execute('select * from memory where logical_key=?',('api_key',)).fetchone()==before

def test_old_sensitive_memory_is_blocked_from_worker_and_export(tmp_path):
    import asyncio,json
    from zipfile import ZipFile
    from alienape.agent import Agent
    from alienape.backends import MockBackend
    from alienape.config import Settings
    from alienape.tools import Workspace
    from alienape.diagnostics import DiagnosticStore
    store=MemoryStore(tmp_path/'m.db','p');store.put('engine','FACT','Godot 4')
    secret='SYNTHETIC_MANUAL_1F52_BB'
    with sqlite3.connect(store.database) as db:
        db.execute("INSERT INTO memory(project_id,logical_key,type,content,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",('p','api_key','FACT',secret,'manual','2020','2020'))
        before=db.execute("select * from memory where logical_key='api_key'").fetchone()
    settings=Settings();settings.diagnostic_trace=True
    backend=MockBackend([{'role':'assistant','content':'Ich kann beim Projekt helfen.'}],delay=0)
    agent=Agent(backend,Workspace(tmp_path),settings,memory=store);agent.diagnostic_store=DiagnosticStore(tmp_path/'diag')
    answer=asyncio.run(agent.run('Erkläre engine und api_key im Projekt'))
    assert secret not in answer and secret not in json.dumps(backend.calls)
    assert 'Godot 4' in json.dumps(backend.calls)
    dest=tmp_path/'bundle.zip';agent.diagnostic_store.export(dest)
    with ZipFile(dest) as z:assert all(secret.encode() not in z.read(n) for n in z.namelist())
    with sqlite3.connect(store.database) as db:assert db.execute("select * from memory where logical_key='api_key'").fetchone()==before
    q=Agent(MockBackend(delay=0),Workspace(tmp_path),Settings(),memory=store)
    result=asyncio.run(q.run('sag mir meinen api key'))
    assert secret not in result
