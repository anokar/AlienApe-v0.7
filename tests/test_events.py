import asyncio
import json
from alienape.events import EventLog
from alienape.controller import ResourceController
from alienape.agent import Agent
from alienape.backends import MockBackend
from alienape.tools import Workspace
from alienape.config import Settings


def test_event_log_and_resource_counts(tmp_path):
    (tmp_path / "input.txt").write_text("Hello")
    log = EventLog(tmp_path / "logs", "project", "session")
    agent = Agent(MockBackend(delay=0), Workspace(tmp_path), Settings(), observe=log.write)
    asyncio.run(agent.run('Lies "input.txt" und beantworte die Frage zum Inhalt.'))
    events = [json.loads(line) for line in (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "FILE_READ" for e in events)
    assert all({"timestamp", "session_id", "project_id", "step", "details", "duration", "resources"} <= e.keys() for e in events)
    assert (tmp_path / "logs/activity.log").read_text(encoding="utf-8")
    counts = agent.resources.snapshot()
    assert counts["model_calls"] == 2
    assert counts["tool_calls"] == counts["file_reads"] == 1
    assert counts["bytes_read"] == 5
    assert counts["input_tokens_estimate"] > 0


def test_cache_invalidation(tmp_path):
    path = tmp_path / "x"
    path.write_text("old")
    controller = ResourceController()
    controller.remember_read(path, {"content": "old"})
    assert controller.cached_read(path) == {"content": "old"}
    path.write_text("new content")
    assert controller.cached_read(path) is None


def test_smalltalk_does_not_need_tool_protocol():
    controller = ResourceController()
    assert not controller.needs_tools("ah cool!")
    assert not controller.needs_tools("Danke.")
    assert controller.needs_tools('Danke, lies "hello.txt"')
