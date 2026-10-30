import asyncio
from alienape.permissions import PermissionManager
from alienape.agent import Agent
from alienape.backends import MockBackend, tool_reply
from alienape.config import Settings
from alienape.tools import Workspace


def test_block_and_session(tmp_path):
    calls = []
    async def ask(*args):
        calls.append(args)
        return "session"
    manager = PermissionManager({"write_file": "block"})
    assert not asyncio.run(manager.request("write_file", {}, tmp_path / "x", ask))
    assert not asyncio.run(manager.request("run_command", {}, tmp_path, ask))
    assert calls == []
    manager = PermissionManager()
    assert asyncio.run(manager.request("write_file", {}, tmp_path / "x", ask))
    assert asyncio.run(manager.request("write_file", {}, tmp_path / "x", ask))
    assert len(calls) == 1
    assert asyncio.run(manager.request("write_file", {}, tmp_path / "y", ask))
    assert len(calls) == 2


def test_agent_write_approved_and_denied(tmp_path):
    async def allowed(*args):
        return True
    async def denied(*args):
        return False
    def run(permission):
        backend = MockBackend([tool_reply("write_file", {"path": "output.txt", "content": "Hi"}), {"role": "assistant", "content": "Ende"}], delay=0)
        agent = Agent(backend, Workspace(tmp_path), Settings(), permission=permission)
        asyncio.run(agent.run("Schreibe die neue Datei output.txt mit einem generierten Text"))
        return agent
    assert run(denied).resources.counters.file_writes == 0
    assert run(denied).resources.counters.model_calls == 1
    assert not (tmp_path / "output.txt").exists()
    assert run(allowed).resources.counters.file_writes == 1
    assert (tmp_path / "output.txt").read_text() == "Hi"
