import asyncio
import pytest
from alienape.backends import MockBackend, OllamaBackend, LMStudioBackend


def test_mock():
    backend = MockBackend(delay=0)
    assert asyncio.run(backend.list_models()) == ["mock-demo"]
    response = asyncio.run(backend.chat([{"role": "user", "content": 'Lies "hello.txt"'}]))
    assert response["tool_calls"][0]["function"]["name"] == "read_file"


@pytest.mark.parametrize("factory,port", [(OllamaBackend, 11434), (LMStudioBackend, 1234)])
def test_url(factory, port):
    assert factory().base_url == f"http://localhost:{port}/v1"
    assert factory(f"http://localhost:{port}/").base_url.endswith("/v1")
    assert factory(f"http://localhost:{port}/v1/").base_url.endswith("/v1")
    assert factory().get_capabilities()["context_length"] is None


@pytest.mark.parametrize("url", ["http://example.com/v1", "file:///tmp", "http://localhost/evil", "http://u:p@localhost/v1", "http://localhost/v1?secret=1"])
def test_reject_external_url(url):
    with pytest.raises(ValueError):
        OllamaBackend(url)


def test_ollama_http_contract():
    import httpx
    import json
    calls = []
    def handle(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "server-reported-model"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        payload = json.loads(request.content)
        assert payload["model"] == "server-reported-model"
        assert payload["tools"][0]["function"]["name"] == "read_file"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok", "reasoning": "MUST NOT ESCAPE"}}]})
    backend = OllamaBackend(transport=httpx.MockTransport(handle))
    assert asyncio.run(backend.health_check()) == ["server-reported-model"]
    result = asyncio.run(backend.chat([{"role": "user", "content": "Hallo"}], [{"type": "function", "function": {"name": "read_file"}}], "server-reported-model"))
    assert result == {"role": "assistant", "content": "ok"}
    assert str(calls[0].url) == "http://localhost:11434/v1/models"
    assert str(calls[1].url) == "http://localhost:11434/api/show"
    assert str(calls[2].url) == "http://localhost:11434/v1/chat/completions"


def test_ollama_json_tools_for_completion_only_model():
    import httpx
    import json
    from alienape.backends import tool_reply
    def handle(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        body = json.loads(request.content)
        assert "tools" not in body
        assert all(m["role"] != "tool" and "tool_calls" not in m for m in body["messages"])
        assert body["response_format"]["type"] == "json_schema"
        from jsonschema import validate, ValidationError
        reply_schema = body["response_format"]["json_schema"]["schema"]
        with pytest.raises(ValidationError):
            validate({}, reply_schema)
        validate({"answer": "Gern!"}, reply_schema)
        text = json.dumps({"tool": {"name": "read_file", "arguments": {"path": "hello.txt"}}})
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    backend = OllamaBackend(transport=httpx.MockTransport(handle))
    response = asyncio.run(backend.chat([tool_reply("read_file", {"path": "other.txt"}), {"role": "tool", "content": "result"}], tools=[{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}], model="legacy-model"))
    assert response["tool_calls"][0]["function"]["name"] == "read_file"
    assert backend.get_capabilities()["tool_mode"] == "json"


def test_ollama_no_tools_preserves_supplied_system_context():
    import httpx
    import json
    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert "tools" not in body and "response_format" not in body
        assert body["max_tokens"] == 128 and body["temperature"] == 0
        assert len(body["messages"]) == 2
        assert body["messages"][0]["content"] == "PROJECT_MEMORY technical data"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Gern!"}}]})
    backend = OllamaBackend(transport=httpx.MockTransport(handle))
    assert asyncio.run(backend.chat([{"role": "system", "content": "PROJECT_MEMORY technical data"}, {"role": "user", "content": "ah cool"}], tools=[], model="legacy-model"))["content"] == "Gern!"


def test_lmstudio_http_errors_and_cancel():
    import httpx
    async def scenario():
        async def handle(request):
            if request.method == "GET":
                return httpx.Response(503, json={"error": "server offline"})
            await asyncio.sleep(10)
            return httpx.Response(200, json={})
        backend = LMStudioBackend(transport=httpx.MockTransport(handle))
        with pytest.raises(httpx.HTTPStatusError):
            await backend.health_check()
        task = asyncio.create_task(backend.chat([{"role": "user", "content": "test"}], model="loaded-model"))
        await asyncio.sleep(0.02)
        backend.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.5)
    asyncio.run(scenario())


@pytest.mark.parametrize("factory", [OllamaBackend, LMStudioBackend])
def test_explicit_none_mode_preserves_task_packet_and_sends_no_tools(factory):
    import httpx
    import json
    def handle(request):
        body = json.loads(request.content)
        assert "tools" not in body and "response_format" not in body
        assert body["messages"][-1]["content"] == "TaskPacket actual code"
        assert body["max_tokens"] == 1024
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Analyse"}}]})
    backend = factory(transport=httpx.MockTransport(handle))
    result = asyncio.run(backend.chat_with_mode([{"role": "user", "content": "TaskPacket actual code"}],
                                               tools=[{"fake": "never send"}], model="specialist", mode="none"))
    assert result["content"] == "Analyse"


def test_explicit_native_mode_never_silently_uses_json_fallback():
    import httpx
    import json
    def handle(request):
        body = json.loads(request.content)
        assert body["tools"]
        return httpx.Response(400, json={"error": "model does not support tools"})
    backend = OllamaBackend(transport=httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(backend.chat_with_mode([{"role": "user", "content": "task"}], tools=[{"type": "function"}], model="x", mode="native"))


def test_json_fallback_validates_server_output_before_returning_call():
    import httpx
    from jsonschema import ValidationError
    def handle(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tool":{"name":"run_command","arguments":{"command":"bad"}}}'}}]})
    backend = OllamaBackend(transport=httpx.MockTransport(handle))
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
    with pytest.raises(ValidationError):
        asyncio.run(backend.chat_with_mode([{"role": "user", "content": "task"}], tools, "x", "json_fallback"))
