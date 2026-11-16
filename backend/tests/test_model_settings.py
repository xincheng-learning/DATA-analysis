"""Real OS encryption, persisted configuration and credential boundary tests."""
from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from app import model_settings
from app.config import settings


@pytest.fixture
def local_settings(monkeypatch, tmp_path):
    if sys.platform != "win32":
        pytest.skip("DPAPI round trips require Windows")
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "llm_base_url", None)
    monkeypatch.setattr(settings, "llm_model", None)
    return tmp_path


def config(**updates):
    return model_settings.ModelSettingsInput.model_validate({
        "base_url": "https://example.invalid/v1", "model": "offline-model",
        "api_key": "offline-test-secret", **updates,
    })


def test_key_is_os_encrypted_and_reloadable_without_plaintext(local_settings):
    model_settings.save_model_settings(config())
    saved = (local_settings / "model-settings.bin").read_bytes()
    assert b"offline-test-secret" not in saved
    assert b"offline-model" not in saved
    restored = model_settings.load_saved_settings()
    assert restored.api_key.get_secret_value() == "offline-test-secret"
    assert restored.model == "offline-model"
    public = model_settings.get_model_status()
    assert public["configured"] and public["encrypted"] and public["source"] == "web"
    assert "offline-test-secret" not in str(public)


def test_blank_key_reuses_only_the_same_endpoint(local_settings):
    model_settings.save_model_settings(config())
    model_settings.save_model_settings(config(api_key=None, model="other-model"))
    assert model_settings.load_saved_settings().model == "other-model"
    with pytest.raises(ValueError, match="重新输入"):
        model_settings.save_model_settings(config(api_key=None, base_url="https://other.invalid/v1"))
    assert model_settings.load_saved_settings().base_url == "https://example.invalid/v1"


@pytest.mark.parametrize("url", [
    "http://example.invalid/v1", "https://user:secret@example.invalid/v1",
    "https://example.invalid/v1?api_key=secret", "https://example.invalid/v1#secret",
    "file:///tmp/model", "https://example.invalid:bad/v1", "https://example.invalid/v1/chat/completions",
])
def test_dangerous_or_incorrect_endpoints_are_rejected(url):
    with pytest.raises(ValidationError):
        config(base_url=url)


def test_local_http_models_are_allowed():
    assert config(base_url="http://127.0.0.1:11434/v1/").base_url == "http://127.0.0.1:11434/v1"


def test_corrupted_secret_fails_closed_and_can_be_replaced(local_settings):
    model_settings.save_model_settings(config())
    (local_settings / "model-settings.bin").write_bytes(b"not-dpapi-data")
    with pytest.raises(model_settings.SecretStorageError):
        model_settings.load_saved_settings()
    model_settings.save_model_settings(config(api_key="replacement-secret"))
    assert model_settings.load_saved_settings().api_key.get_secret_value() == "replacement-secret"


def test_atomic_save_failure_preserves_previous_config(local_settings, monkeypatch):
    model_settings.save_model_settings(config())
    def failed_replace(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(model_settings.os, "replace", failed_replace)
    with pytest.raises(model_settings.SecretStorageError):
        model_settings.save_model_settings(config(api_key="replacement-secret"))
    assert model_settings.load_saved_settings().api_key.get_secret_value() == "offline-test-secret"
    assert list(local_settings.glob("*.tmp")) == []


def test_real_structured_connection_probe_uses_no_business_data(monkeypatch):
    import json

    import httpx
    from langchain_openai import ChatOpenAI

    from app import llm

    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "offline-probe", "object": "chat.completion", "created": 0, "model": "offline-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "probe", "type": "function", "function": {
                        "name": "ConnectionProbe", "arguments": '{"ready": true}',
                    },
                }],
            }}],
        })
    transport = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False)
    real_client = ChatOpenAI(
        api_key="offline-only", base_url="https://example.invalid/v1", model="offline-model",
        http_client=transport, max_retries=0, max_tokens=64,
    )
    monkeypatch.setattr(llm, "build_compatible_client", lambda *args, **kwargs: real_client)
    llm.check_model_connection(config())
    assert len(requests) == 1
    assert requests[0].get("max_completion_tokens", requests[0].get("max_tokens")) == 64
    assert requests[0]["messages"][0]["content"].startswith("Connection check only.")
    assert "tools" in requests[0]
    assert transport.is_closed


def test_unsupported_os_never_falls_back_to_plaintext(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    monkeypatch.setattr(model_settings.sys, "platform", "not-windows")
    with pytest.raises(model_settings.SecretStorageError, match="Windows"):
        model_settings.save_model_settings(config())
    assert list(tmp_path.iterdir()) == []


def test_thinking_settings_are_encrypted_persisted_and_old_configs_still_load(local_settings):
    model_settings.save_model_settings(config(base_url="https://api.deepseek.com", model="deepseek-flash",
        thinking_mode="enabled", reasoning_effort="low"))
    restored = model_settings.load_saved_settings()
    assert restored.thinking_mode == "enabled" and restored.reasoning_effort == "low"
    public = model_settings.get_model_status()
    assert public["thinking_mode"] == "enabled" and public["reasoning_effort"] == "low"
    model_settings.save_model_settings(config(base_url="https://api.deepseek.com", model="deepseek-flash",
        api_key=None, thinking_mode="disabled"))
    assert model_settings.load_saved_settings().thinking_mode == "disabled"
    assert model_settings.load_saved_settings().api_key.get_secret_value() == "offline-test-secret"
    import json
    (local_settings / "model-settings.bin").write_bytes(model_settings._dpapi(json.dumps({
        "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": "old-format-secret",
    }).encode()))
    assert model_settings.load_saved_settings().thinking_mode == "auto"


def test_thinking_settings_reject_invalid_modes_and_unsupported_endpoints():
    for update in ({"thinking_mode": "mystery"}, {"reasoning_effort": "unbounded"}, {"thinking_mode": "enabled"}):
        with pytest.raises(ValidationError):
            config(**update)


def test_deepseek_analysis_has_room_for_reasoning_and_keeps_explicit_probe_caps():
    from app.llm import build_compatible_client
    import httpx
    transport=httpx.Client(transport=httpx.MockTransport(lambda request:pytest.fail('No network call expected')))
    try:
        analysis=build_compatible_client('https://api.deepseek.com','test-key','deepseek-flash',http_client=transport)
        probe=build_compatible_client('https://api.deepseek.com','test-key','deepseek-flash',http_client=transport,max_tokens=64)
        assert analysis.max_tokens==16384
        assert probe.max_tokens==64
    finally:
        transport.close()
