import asyncio
import pytest
from alienape.agent import Agent, Session
from alienape.backends import MockBackend
from alienape.config import Settings
from alienape.controller import ResourceController
from alienape.memory import MemoryStore
from alienape.tools import Workspace


class NoModelBackend(MockBackend):
    async def chat(self, *args, **kwargs):
        raise AssertionError("Memory-Übersicht darf kein Modell und keine Datei benötigen")


@pytest.mark.parametrize("question", [
    "was hast du dir alles gemerkt?", "Was hast du dir gemerkt?", "an was müssen wir uns erinern?",
    "Woran müssen wir uns erinnern?", "Zeige mir alle Erinnerungen", "Was ist im Memory?",
    "Was haben wir uns gespeichert?", "what do you remember?",
])
def test_reported_memory_questions_use_database_without_model(tmp_path, question):
    store = MemoryStore(tmp_path / "memory.db", "project")
    store.put("godot-version", "USER_DECISION", "Wir verwenden Godot 4.")
    store.put("sprache", "PROJECT_RULE", "Wir schreiben auf Deutsch.")
    session = Session()
    session.clear()
    agent = Agent(NoModelBackend(), Workspace(tmp_path), Settings(), session, store)
    answer = asyncio.run(agent.run(question))
    assert "Godot 4" in answer and "Deutsch" in answer
    assert "Datei" not in answer and "txt" not in answer
    assert agent.resources.counters.model_calls == agent.resources.counters.file_reads == agent.resources.counters.tool_calls == 0
    assert agent.resources.counters.memory_reads == 1
    assert session.history[-1]["content"] == answer


def test_overview_filters_project_superseded_disabled_and_limits(tmp_path):
    database = tmp_path / "memory.db"
    store = MemoryStore(database, "project")
    store.put("engine", "USER_DECISION", "Godot 3")
    store.put("engine", "USER_DECISION", "Godot 4")
    inactive = store.put("hidden", "FACT", "inactive secret")
    store.update_flags(inactive["id"], active=False)
    MemoryStore(database, "other").put("other", "FACT", "another project")
    rows, total = MemoryStore(database, "project").overview()
    assert total == 1 and [r["content"] for r in rows] == ["Godot 4"]
    assert store.retrieve("Godot")[0]["access_count"] == 1
    for index in range(52):
        store.put(f"key-{index}", "FACT", f"Fact {index}")
    rows, total = store.overview()
    assert total == 53 and len(rows) == 50


def test_empty_and_disabled_memory_are_honest(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", "project")
    settings = Settings()
    agent = Agent(NoModelBackend(), Workspace(tmp_path), settings, memory=store)
    assert "noch keine" in asyncio.run(agent.run("was hast du dir alles gemerkt?"))
    settings.memory.enabled = False
    assert "ausgeschaltet" in asyncio.run(agent.run("was hast du dir alles gemerkt?"))


def test_other_tasks_keep_normal_routing():
    controller = ResourceController()
    assert not controller.wants_memory_overview('Schreibe "was hast du dir gemerkt" in "test.txt"')
    assert not controller.wants_memory_overview("Was steht in der Datei memory.txt?")
    assert not controller.wants_memory_overview("Merke dir: Godot 4.")
