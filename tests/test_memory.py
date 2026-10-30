import asyncio
from alienape.memory import MemoryStore
from alienape.backends import MockBackend
from alienape.agent import Agent, Session
from alienape.config import Settings
from alienape.tools import Workspace


def test_memory_store_dedup_supersede_retrieval(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", "project")
    first = store.put("engine", "USER_DECISION", "Godot 3")
    assert store.put("engine", "USER_DECISION", "Godot 3")["changed"] is False
    assert len(store.all()) == 1
    second = store.put("engine", "USER_DECISION", "Godot 4")
    assert second["superseded_ids"] == [first["id"]]
    assert len(store.all()) == 2
    assert store.retrieve("Welche Godot-Version?")[0]["content"] == "Godot 4"
    assert store.all()[0]["access_count"] == 1
    assert not MemoryStore(tmp_path / "memory.db", "other").retrieve("Godot")
    store.update_flags(second["id"], active=False)
    assert not store.retrieve("Godot")
    store.delete(first["id"])
    assert len(store.all()) == 1


def test_end_to_end_persistent_memory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "hello.txt").write_text("AlienApe Testdatei", encoding="utf-8")
    store = MemoryStore(tmp_path / "memory.db", str(project))
    session = Session()
    agent = Agent(MockBackend(delay=0), Workspace(project), Settings(), session, store)
    assert "AlienApe Testdatei" in asyncio.run(agent.run('Lies "hello.txt"'))
    assert "Gemerkt" in asyncio.run(agent.run("Merke dir: Wir verwenden Godot 4."))
    session.clear()
    del agent, store
    # New connection, backend and session: the answer can only originate from SQLite.
    new = Agent(MockBackend(delay=0), Workspace(project), Settings(), Session(), MemoryStore(tmp_path / "memory.db", str(project)))
    assert "Godot 4" in asyncio.run(new.run("Welche Godot-Version verwenden wir?"))
    assert new.resources.counters.memory_reads == 1
