"""Capture real SDK requests offline; no service credentials or paid calls."""
import json

import httpx
import pytest
from pydantic import BaseModel

from app import llm
from app.model_settings import ModelSettingsInput


def tool_response(name, arguments):
    return httpx.Response(200, json={
        "id": "offline", "object": "chat.completion", "created": 0, "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "offline-call", "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments),
            }}],
        }}],
    })


def test_deepseek_probe_uses_supported_tool_mode_and_token_parameter(monkeypatch):
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("thinking", {}).get("type", "enabled") != "disabled" and isinstance(payload.get("tool_choice"), dict):
            return httpx.Response(400, json={"error": {"message": "thinking mode rejects named tool choice"}})
        assert payload["max_tokens"] == 64
        assert "max_completion_tokens" not in payload
        return tool_response("ConnectionProbe", {"ready": True})
    transport = httpx.Client(transport=httpx.MockTransport(respond))
    build = llm.build_compatible_client
    monkeypatch.setattr(llm, "build_compatible_client", lambda *args, **kwargs: build(*args, **kwargs, http_client=transport))
    llm.check_model_connection(ModelSettingsInput(
        base_url="https://api.deepseek.com", model="deepseek-flash", api_key="offline-only",
    ))
    assert len(requests) == 1 and transport.is_closed


def test_same_adapter_covers_agent_structured_outputs_without_disabling_plain_reasoning():
    class Decision(BaseModel):
        route: str
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("tools"):
            return tool_response("Decision", {"route": "sql"})
        return httpx.Response(200, json={"id": "plain", "object": "chat.completion", "created": 0,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "SELECT 1"}}]})
    transport = httpx.Client(transport=httpx.MockTransport(respond))
    client = llm.build_compatible_client("https://api.deepseek.com", "offline-only", "deepseek-flash", http_client=transport)
    try:
        assert client.invoke("write SQL").content == "SELECT 1"
        assert client.with_structured_output(Decision).invoke("route").route == "sql"
        assert "thinking" not in requests[0]
        assert requests[1]["thinking"] == {"type": "disabled"}
        assert len(requests) == 2
    finally:
        transport.close()


def test_other_compatible_endpoints_keep_their_existing_wire_format():
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return tool_response("ConnectionProbe", {"ready": True})
    transport = httpx.Client(transport=httpx.MockTransport(respond))
    client = llm.build_compatible_client("https://api.deepseek.com.example.invalid/v1", "offline-only", "other", http_client=transport, max_tokens=64)
    try:
        client.invoke("hello")
        assert "thinking" not in requests[0]
        assert requests[0]["max_completion_tokens"] == 64
    finally:
        transport.close()


@pytest.mark.parametrize("mode, expected_choice, thinking", [("enabled", "auto", "enabled"), ("disabled", "named", "disabled")])
def test_explicit_thinking_mode_controls_both_plain_and_structured_requests(mode, expected_choice, thinking):
    class Decision(BaseModel):
        route: str
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("tools"):
            return tool_response("Decision", {"route": "sql"})
        return httpx.Response(200, json={"id": "plain", "object": "chat.completion", "created": 0,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "SELECT 1"}}]})
    transport = httpx.Client(transport=httpx.MockTransport(respond))
    client = llm.build_compatible_client("https://api.deepseek.com", "offline-only", "deepseek-flash",
                                        http_client=transport, thinking_mode=mode, reasoning_effort="low")
    try:
        client.invoke("write SQL")
        assert client.with_structured_output(Decision).invoke("route").route == "sql"
        assert all(payload["thinking"] == {"type": thinking} for payload in requests)
        if expected_choice == "auto":
            assert requests[1]["tool_choice"] == "auto"
            assert all(payload["reasoning_effort"] == "low" for payload in requests)
        else:
            assert isinstance(requests[1]["tool_choice"], dict)
            assert all("reasoning_effort" not in payload for payload in requests)
    finally:
        transport.close()


def test_enabled_probe_does_not_silently_downgrade_or_retry_when_no_tool_is_returned(monkeypatch):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "plain", "object": "chat.completion", "created": 0,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "ready"}}]})
    transport = httpx.Client(transport=httpx.MockTransport(respond))
    build = llm.build_compatible_client
    monkeypatch.setattr(llm, "build_compatible_client", lambda *args, **kwargs: build(*args, **kwargs, http_client=transport))
    with pytest.raises(ValueError):
        llm.check_model_connection(ModelSettingsInput(base_url="https://api.deepseek.com", model="deepseek-flash",
                                  api_key="offline-only", thinking_mode="enabled", reasoning_effort="low"))
    assert len(requests) == 1 and requests[0]["thinking"] == {"type": "enabled"}
    assert requests[0]["max_tokens"] == 1024 and transport.is_closed
