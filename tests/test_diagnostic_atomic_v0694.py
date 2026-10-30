import asyncio,uuid
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch
import pytest
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.diagnostics import DiagnosticStore
from alienape.security import redactor
from alienape.tools import Workspace

@pytest.fixture
def recorded(tmp_path):
    value='DIAG_CANARY_NOT_REAL_'+uuid.uuid4().hex;redactor.register(value)
    settings=Settings();settings.diagnostic_trace=True
    agent=Agent(MockBackend([{'role':'assistant','content':'answer '+value}],delay=0),Workspace(tmp_path),settings)
    store=DiagnosticStore(tmp_path/'diag');agent.diagnostic_store=store
    asyncio.run(agent.run('Erkläre Python '+value))
    return store,value

def assert_no_partial(path):
    assert not path.exists()
    assert not list(path.parent.glob('.alienape-diagnostic-*.tmp'))

def test_success_and_canary(recorded,tmp_path):
    store,value=recorded;target=tmp_path/'ok.zip';assert store.export(target)==target
    with ZipFile(target) as z:
        assert set(z.namelist())=={'CHAT.md','TRACE.jsonl','SUMMARY.json','MANIFEST.json'}
        assert z.testzip() is None
        assert all(value.encode() not in z.read(n) for n in z.namelist())
    assert not list(tmp_path.glob('.alienape-diagnostic-*.tmp'))

def test_second_entry_failure_and_retry(recorded,tmp_path,monkeypatch):
    store,_=recorded;target=tmp_path/'out.zip';original=ZipFile.writestr;calls=0
    def fail(z,*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('forced write failure')
        return original(z,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(ZipFile,'writestr',fail)
        with pytest.raises(OSError,match='forced write failure'):store.export(target)
    assert_no_partial(target)
    assert store.export(target).is_file()

def test_finalization_failure(recorded,tmp_path,monkeypatch):
    store,_=recorded;target=tmp_path/'close.zip';original=ZipFile.close;failed=False
    def fail(z):
        nonlocal failed
        original(z)
        if not failed:
            failed=True
            raise OSError('forced finalization failure')
    with monkeypatch.context() as m:
        m.setattr(ZipFile,'close',fail)
        with pytest.raises(OSError,match='forced finalization failure'):store.export(target)
    assert_no_partial(target)

def test_publish_failure_preserves_existing_file(recorded,tmp_path,monkeypatch):
    store,_=recorded;target=tmp_path/'unrelated.zip';target.write_bytes(b'unrelated file')
    with pytest.raises(FileExistsError):store.export(target)
    assert target.read_bytes()==b'unrelated file'
    assert not list(tmp_path.glob('.alienape-diagnostic-*.tmp'))
    absent=tmp_path/'absent.zip'
    def fail(*_):raise OSError('forced publish failure')
    monkeypatch.setattr('alienape.diagnostics.os.link',fail)
    with pytest.raises(OSError,match='forced publish failure'):store.export(absent)
    assert_no_partial(absent)
    assert target.read_bytes()==b'unrelated file'

def test_cleanup_error_is_recorded(recorded,tmp_path,monkeypatch):
    store,_=recorded;target=tmp_path/'cleanup.zip'
    def fail_publish(*_):raise OSError('forced publish failure')
    def fail_cleanup(*_):raise PermissionError('forced cleanup failure')
    monkeypatch.setattr('alienape.diagnostics.os.link',fail_publish)
    monkeypatch.setattr('alienape.diagnostics.os.unlink',fail_cleanup)
    with pytest.raises(OSError,match='forced publish failure') as caught:store.export(target)
    assert not target.exists()
    assert 'forced cleanup failure' in store.last_error and caught.value.__notes__
