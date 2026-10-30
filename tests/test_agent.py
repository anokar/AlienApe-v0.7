import asyncio
import threading
import pytest
from alienape.agent import Agent, LoopStopped
from alienape.backends import MockBackend, tool_reply
from alienape.config import Settings
from alienape.context import ContextManager, size
from alienape.tools import Workspace


def test_context_budget():
    rounds = [[tool_reply("read_file", {"path": "x"}), {"role": "tool", "tool_call_id": "x", "content": "x" * 20000}]]
    messages = ContextManager(2048).build("Aktuelle Aufgabe", [{"role": "user", "content": "alt" * 10000}], [], rounds)
    assert size(messages) <= 2048
    assert any(m.get("content") == "Aktuelle Aufgabe" for m in messages)
    assert len([m for m in messages if m["role"] == "tool"]) == 1
    with pytest.raises(ValueError):
        ContextManager(2048).build("x" * 3000)


def test_context_impossible_tool_group_fails_without_loop():
    rounds = [[tool_reply("write_file", {"path": "x", "content": "x" * 5000}), {"role": "tool", "tool_call_id": "x", "content": "x" * 5000}]]
    with pytest.raises(ValueError, match="zu groß"):
        ContextManager(2048).build("Aktuelle Aufgabe", rounds=rounds)


def test_agent_mock_reads(tmp_path):
    (tmp_path / "hello.txt").write_text("Hallo AlienApe", encoding="utf-8")
    agent = Agent(MockBackend(delay=0), Workspace(tmp_path), Settings())
    answer = asyncio.run(agent.run('Lies "hello.txt" und beantworte die Frage zum Inhalt.'))
    assert "Hallo AlienApe" in answer
    assert agent.resources.counters.file_reads == 1
    assert agent.resources.counters.model_calls == 2
    assert agent.resources.counters.bytes_read == 14


def test_max_steps(tmp_path):
    settings = Settings()
    settings.agent.max_steps = 1
    agent = Agent(MockBackend([tool_reply("list_files", {"path": "."})], delay=0), Workspace(tmp_path), settings)
    with pytest.raises(LoopStopped, match="Schrittlimit"):
        asyncio.run(agent.run("Nutze list_files wiederholt im Workspace"))


def test_repeated_tool_loop(tmp_path):
    responses = [tool_reply("list_files", {"path": "."}) for _ in range(5)]
    agent = Agent(MockBackend(responses, delay=0), Workspace(tmp_path), Settings())
    with pytest.raises(LoopStopped, match="identische"):
        asyncio.run(agent.run("Nutze list_files wiederholt im Workspace"))
    assert agent.resources.counters.model_calls == 3


def test_cancel_prevents_tools(tmp_path):
    async def scenario():
        stopped = threading.Event()
        backend = MockBackend([tool_reply("write_file", {"path": "danger", "content": "no"})], delay=5)
        agent = Agent(backend, Workspace(tmp_path), Settings(), stopped=stopped)
        task = asyncio.create_task(agent.run("write"))
        await asyncio.sleep(0.02)
        stopped.set()
        backend.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        assert not (tmp_path / "danger").exists()
    asyncio.run(scenario())


def test_unknown_tool_is_feedback(tmp_path):
    backend = MockBackend([tool_reply("run_command", {"path": "."}), {"role": "assistant", "content": "Fehler verstanden"}], delay=0)
    agent = Agent(backend, Workspace(tmp_path), Settings())
    assert asyncio.run(agent.run("Nutze ein Werkzeug im Workspace")) == "Fehler verstanden"
    assert "Unbekanntes" in backend.calls[-1][-1]["content"]
