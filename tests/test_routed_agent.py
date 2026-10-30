import asyncio
import json
import threading
import pytest
from alienape.agent import LoopStopped, Session
from alienape.backends import MockBackend, tool_reply
from alienape.config import Settings
from alienape.events import EventLog
from alienape.memory import MemoryStore
from alienape.routed_agent import RoutedAgent, VerifiedModelFailure, ModelOutputError
from alienape.routing import ModelRegistry
from alienape.tools import Workspace
from test_routing import profile


class Pool:
    def __init__(self, models):
        self.models = models

    def get(self, profile):
        return self.models[profile.id]


class RecordingMock(MockBackend):
    def __init__(self, responses, delay=0):
        super().__init__(responses, delay)
        self.requests = []

    async def chat(self, messages, tools=None, model=""):
        self.requests.append({"messages": messages, "tools": tools, "model": model})
        return await super().chat(messages, tools, model)


def answer(text):
    return {"role": "assistant", "content": text}


def setup(tmp_path, model_responses=None, *, coding_mode="none"):
    (tmp_path / "player.py").write_text("def divide(a, b):\n    return a * b\n", encoding="utf-8")
    settings = Settings()
    settings.models = [profile("general", tool_mode="native"), profile("coder", "coding", "medium", tool_mode=coding_mode),
                       profile("reasoner", "reasoning", "high", tool_mode="none")]
    settings.routing.default_model = settings.routing.fallback_model = "general"
    models = {key: RecordingMock(responses) for key, responses in (model_responses or {}).items()}
    for p in settings.models:
        models.setdefault(p.id, RecordingMock([answer("Fertige Antwort")]))
    return settings, ModelRegistry(settings.models), Pool(models)


def make_agent(tmp_path, settings, registry, pool, **kwargs):
    return RoutedAgent(Workspace(tmp_path), settings, registry=registry, pool=pool, **kwargs)


def test_e2e_controller_reads_coder_proposes_permission_then_write(tmp_path):
    proposal = {"answer": "Der Operator muss / sein.", "status": "ok", "proposed_files": [
        {"path": "player.py", "content": "def divide(a, b):\n    return a / b\n"}]}
    settings, registry, pool = setup(tmp_path, {"coder": [answer(json.dumps(proposal))]})
    approvals = []
    async def approve(name, arguments, path):
        approvals.append((name, path.name))
        assert "a * b" in path.read_text()
        return True
    store = MemoryStore(tmp_path / "memory.db", "project")
    store.put("irrelevant", "FACT", "Der Lieblingskuchen ist Schokolade.")
    log = EventLog(tmp_path / "logs", "project")
    session = Session()
    session.history = [{"role": "user", "content": "IRRELEVANT PRIVATE HISTORY"}]
    agent = make_agent(tmp_path, settings, registry, pool, session=session, memory=store, permission=approve, observe=log.write)
    result = asyncio.run(agent.run("Korrigiere den Fehler in player.py"))
    assert "Operator" in result and "Nach Freigabe geschrieben" in result
    assert (tmp_path / "player.py").read_text().endswith("a / b\n")
    assert approvals == [("write_file", "player.py")]
    request = pool.models["coder"].requests[0]
    assert request["tools"] is None
    payload = json.loads(request["messages"][-1]["content"])
    assert payload["relevant_files_or_contents"][0]["path"] == "player.py"
    assert "IRRELEVANT" not in json.dumps(request) and "Schokolade" not in json.dumps(request)
    events = [json.loads(line) for line in (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "MODEL_ROUTED" and e["details"]["to_model"] == "coder" for e in events)
    assert any(e["event_type"] == "TOOL_DELEGATED" for e in events)
    usage = agent.resources.snapshot()["per_model"]["coder"]
    assert usage["model_calls"] == 1 and usage["successful_tasks"] == 1
    assert usage["delegated_tool_calls"] == 2 and usage["tool_calls_requested"] == 0
    assert usage["context_bytes"] > 0


def test_general_summary_no_escalation_and_manual_override(tmp_path):
    settings, registry, pool = setup(tmp_path)
    agent = make_agent(tmp_path, settings, registry, pool)
    assert asyncio.run(agent.run("Fasse diese kurze Notiz zusammen: Hallo Welt")) == "Fertige Antwort"
    assert set(agent.resources.per_model) == {"general"}
    assert agent.resources.per_model["general"].escalations == 0
    agent = make_agent(tmp_path, settings, registry, pool, manual_model="reasoner")
    assert asyncio.run(agent.run("Fasse diese kurze Notiz zusammen: Hallo Welt")) == "Fertige Antwort"
    assert set(agent.resources.per_model) == {"reasoner"}


def test_native_model_keeps_existing_tool_pipeline(tmp_path):
    settings, registry, pool = setup(tmp_path, {"coder": [tool_reply("read_file", {"path": "player.py"}), answer("Fehler gefunden")]}, coding_mode="native")
    agent = make_agent(tmp_path, settings, registry, pool)
    assert asyncio.run(agent.run("Finde den Fehler in player.py")) == "Fehler gefunden"
    assert pool.models["coder"].requests[0]["tools"]
    assert agent.resources.per_model["coder"].tool_calls_requested == 1
    assert agent.resources.counters.file_reads == 1


def test_escalation_once_and_resources_separate(tmp_path):
    failed = answer(json.dumps({"answer": "Kontext/Fähigkeit fehlt", "status": "cannot_complete"}))
    settings, registry, pool = setup(tmp_path, {"coder": [failed], "reasoner": [answer("Korrekte Analyse")]})
    events = []
    registry.profiles["reasoner"].capabilities.append("coding")  # Explicit eligible escalation fixture.
    agent = make_agent(tmp_path, settings, registry, pool, observe=lambda kind, details, *rest: events.append((kind, details)))
    assert asyncio.run(agent.run("Finde den Fehler in player.py")) == "Korrekte Analyse"
    assert len([e for e in events if e[0] == "MODEL_ESCALATED"]) == 1
    change = next(d for k, d in events if k == "MODEL_ESCALATED")
    assert change["from_model"] == "coder" and change["to_model"] == "reasoner" and change["reason"]
    assert agent.resources.per_model["coder"].failed_tasks == 1
    assert agent.resources.per_model["reasoner"].successful_tasks == 1
    assert agent.resources.per_model["coder"].model_calls == agent.resources.per_model["reasoner"].model_calls == 1
    assert agent.packets[-1].source_model == "coder"


def test_escalation_limit_and_manual_lock(tmp_path):
    failed = answer(json.dumps({"answer": "Fähigkeit fehlt", "status": "cannot_complete"}))
    settings, registry, pool = setup(tmp_path, {"coder": [failed], "reasoner": [failed]})
    registry.profiles["reasoner"].capabilities.append("coding")  # Explicit eligible escalation fixture.
    agent = make_agent(tmp_path, settings, registry, pool)
    with pytest.raises(VerifiedModelFailure):
        asyncio.run(agent.run("Debug player.py"))
    assert sum(v.escalations for v in agent.resources.per_model.values()) == 1
    settings, registry, pool = setup(tmp_path, {"coder": [failed]})
    agent = make_agent(tmp_path, settings, registry, pool, manual_model="coder")
    with pytest.raises(VerifiedModelFailure):
        asyncio.run(agent.run("Debug player.py"))
    assert set(agent.resources.per_model) == {"coder"}


def test_delegate_write_denied_never_escalates(tmp_path):
    proposal = {"answer": "Vorschlag", "proposed_files": [{"path": "player.py", "content": "changed"}]}
    settings, registry, pool = setup(tmp_path, {"coder": [answer(json.dumps(proposal))]})
    async def deny(*args):
        return False
    agent = make_agent(tmp_path, settings, registry, pool, permission=deny)
    assert "abgelehnt" in asyncio.run(agent.run("Korrigiere Fehler in player.py"))
    assert "a * b" in (tmp_path / "player.py").read_text()
    assert agent.resources.counters.file_writes == 0
    assert set(agent.resources.per_model) == {"coder"}


def test_none_model_cannot_execute_unrequested_path(tmp_path):
    proposal = {"answer": "Vorschlag", "proposed_files": [{"path": "../outside.txt", "content": "changed"}]}
    settings, registry, pool = setup(tmp_path, {"coder": [answer(json.dumps(proposal))]})
    agent = make_agent(tmp_path, settings, registry, pool)
    with pytest.raises(PermissionError):
        asyncio.run(agent.run("Finde Fehler in player.py"))
    assert agent.resources.counters.file_writes == 0


def test_plain_answer_is_not_json_just_because_it_starts_with_brace(tmp_path):
    settings, registry, pool = setup(tmp_path, {"coder": [answer("{bad"), answer("{bad")], "reasoner": [answer("Analyse") ]})
    agent = make_agent(tmp_path, settings, registry, pool)
    assert asyncio.run(agent.run("Debug player.py")) == "{bad"
    assert agent.resources.per_model["coder"].invalid_structured_outputs == 0
    assert agent.resources.per_model["coder"].model_calls == 1


def test_external_verifier_failure_can_escalate_before_write(tmp_path):
    settings, registry, pool = setup(tmp_path)
    seen = []
    def verify(p, answer, packet):
        seen.append(p.id)
        return p.id == "reasoner"
    registry.profiles["reasoner"].capabilities.append("coding")  # Explicit eligible escalation fixture.
    agent = make_agent(tmp_path, settings, registry, pool, verify=verify)
    assert asyncio.run(agent.run("Debug player.py")) == "Fertige Antwort"
    assert seen == ["coder", "reasoner"]


def test_stop_while_specialist_is_running(tmp_path):
    async def scenario():
        settings, registry, pool = setup(tmp_path)
        pool.models["coder"] = RecordingMock([answer("Should never arrive")], delay=10)
        stop = threading.Event()
        agent = make_agent(tmp_path, settings, registry, pool, stopped=stop)
        task = asyncio.create_task(agent.run("Debug player.py"))
        await asyncio.sleep(0.03)
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
        assert agent.resources.counters.file_writes == 0
        assert agent.resources.per_model["coder"].cancelled_tasks == 1
    asyncio.run(scenario())


def test_read_only_request_never_applies_proposals(tmp_path):
    proposal = {"answer": "Vorschlag", "proposed_files": [{"path": "player.py", "content": "changed"}]}
    settings, registry, pool = setup(tmp_path, {"coder": [answer(json.dumps(proposal))]})
    async def unexpected_permission(*args):
        raise AssertionError("Eine ausdrückliche Analyse darf keine Schreibfreigabe anfragen")
    agent = make_agent(tmp_path, settings, registry, pool, permission=unexpected_permission)
    assert "nicht angewendet" in asyncio.run(agent.run("Analysiere nur player.py, ändere keine Datei"))
    assert agent.resources.counters.file_writes == 0


def test_validated_proposal_with_trailing_text_never_executes_second_object(tmp_path):
    proposal = {"answer": "Korrektur", "proposed_files": [{"path": "player.py", "content": "fixed"}]}
    extra = {"answer": "hidden", "proposed_files": [{"path": "../never.txt", "content": "no"}]}
    settings, registry, pool = setup(tmp_path, {"coder": [answer(json.dumps(proposal) + '\nErläuterung\n' + json.dumps(extra))]})
    calls = []
    async def approve(name, arguments, path):
        calls.append(path.name)
        return True
    agent = make_agent(tmp_path, settings, registry, pool, permission=approve)
    from alienape.completion import CompletionRejected
    with pytest.raises(CompletionRejected):asyncio.run(agent.run("Korrigiere player.py"))
    assert calls == []
    assert "a * b" in (tmp_path / "player.py").read_text()
    assert not (tmp_path.parent / "never.txt").exists()


def test_total_step_limit_and_escalation_disabled(tmp_path):
    settings, registry, pool = setup(tmp_path, {"coder": [answer("{bad"), answer("{bad")]})
    settings.agent.max_steps = 2
    agent = make_agent(tmp_path, settings, registry, pool)
    from alienape.completion import CompletionRejected
    with pytest.raises(CompletionRejected):
        asyncio.run(agent.run("Debug player.py und antworte als JSON"))
    assert agent.resources.counters.model_calls == 1
    assert set(agent.resources.per_model) == {"coder"}
    settings, registry, pool = setup(tmp_path, {"coder": [answer('{"answer":"missing","status":"cannot_complete"}')]})
    settings.routing.max_escalations_per_task = 0
    agent = make_agent(tmp_path, settings, registry, pool)
    with pytest.raises(VerifiedModelFailure):
        asyncio.run(agent.run("Debug player.py"))
    assert set(agent.resources.per_model) == {"coder"}


def test_server_failure_falls_back_to_general_and_logs(tmp_path):
    import httpx
    class Offline(RecordingMock):
        async def chat(self, *args, **kwargs):
            raise httpx.ConnectError("offline")
    settings, registry, pool = setup(tmp_path)
    pool.models["coder"] = Offline([])
    pool.models['general']=RecordingMock([tool_reply('read_file',{'path':'player.py'}),answer('Fertige Antwort')])
    log = EventLog(tmp_path / "logs", "test")
    agent = make_agent(tmp_path, settings, registry, pool, observe=log.write)
    assert asyncio.run(agent.run("Debug player.py")) == "Fertige Antwort"
    assert registry.availability["coder"] is False
    events = [json.loads(line) for line in (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    routed = [event for event in events if event["event_type"] == "MODEL_ROUTED"]
    assert routed[-1]["details"]["from_model"] == "coder"
    assert routed[-1]["details"]["to_model"] == "general"


def test_escalation_is_persisted_as_jsonl(tmp_path):
    settings, registry, pool = setup(tmp_path, {"coder": [answer('{"answer":"missing","status":"cannot_complete"}')]})
    log = EventLog(tmp_path / "logs", "test")
    registry.profiles["reasoner"].capabilities.append("coding")  # Explicit eligible escalation fixture.
    agent = make_agent(tmp_path, settings, registry, pool, observe=log.write)
    asyncio.run(agent.run("Debug player.py"))
    events = [json.loads(line) for line in (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    change = next(e for e in events if e["event_type"] == "MODEL_ESCALATED")
    assert change["details"]["from_model"] == "coder" and change["details"]["to_model"] == "reasoner"
    assert set(events[-1]["resources"]["per_model"]) == {"coder", "reasoner"}


def test_none_can_return_regular_json_data(tmp_path):
    settings, registry, pool = setup(tmp_path, {"coder": [answer('{"count": 5, "items": []}')]})
    agent = make_agent(tmp_path, settings, registry, pool, manual_model="coder")
    assert json.loads(asyncio.run(agent.run("Erzeuge ein JSON-Datenobjekt")))["count"] == 5


def test_new_document_without_filename_requires_clarification(tmp_path):
    proposal = {"answer": "Neue Notiz", "proposed_files": [{"path": "document.txt", "content": "Hello World"}]}
    settings, registry, pool = setup(tmp_path, {"coder": [answer(json.dumps(proposal))]})
    calls = []
    async def approve(name, arguments, path):
        assert not path.exists()
        calls.append(path.name)
        return True
    agent = make_agent(tmp_path, settings, registry, pool, manual_model="coder", permission=approve)
    asyncio.run(agent.run("make a new text document with hello world inside"))
    assert not calls and not (tmp_path / "document.txt").exists()
    assert agent.session.task_state.pending_task and agent.resources.counters.model_calls==0
