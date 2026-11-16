"""Model settings are validated and clients constructed without external calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import llm
from app.config import Settings


@pytest.fixture
def compatible_settings(monkeypatch):
    config = SimpleNamespace(
        llm_provider="openai_compatible",
        llm_base_url="https://example.invalid/v1",
        llm_api_key="offline-test-only",
        llm_model="test-model",
    )
    monkeypatch.setattr(llm, "settings", config)
    llm.reset_cache()
    yield config
    llm.reset_cache()


def test_compatible_endpoint_and_model_reach_client(compatible_settings):
    client = llm.get_chat_llm()
    assert client.openai_api_base == "https://example.invalid/v1"
    assert client.model_name == "test-model"
    assert client.max_retries == 0
    assert client.http_client.follow_redirects is False


@pytest.mark.parametrize("field", ["llm_base_url", "llm_api_key", "llm_model"])
def test_missing_compatible_config_is_rejected(compatible_settings, field):
    setattr(compatible_settings, field, None)
    with pytest.raises(RuntimeError, match="LLM_BASE_URL.*LLM_API_KEY.*LLM_MODEL"):
        llm.get_chat_llm()


def test_configuration_aliases_load_without_an_env_file():
    config = Settings(
        _env_file=None,
        LLM_BASE_URL="https://example.invalid/v1",
        LLM_API_KEY="offline-test-only",
        LLM_MODEL="test-model",
    )
    assert config.llm_base_url == "https://example.invalid/v1"
    assert config.llm_model == "test-model"


@pytest.mark.parametrize('mode',['auto','enabled','disabled'])
def test_official_json_output_preserves_quotes_and_thinking_without_retries(monkeypatch,mode):
    import json
    import httpx
    from pydantic import BaseModel
    from langchain_core.messages import HumanMessage
    class Result(BaseModel):
        note:str
    requests=[]
    def respond(request):
        payload=json.loads(request.content);requests.append(payload)
        return httpx.Response(200,json={'id':'test','object':'chat.completion','created':1,'model':'deepseek-flash',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'note':'" SP " → "SP"'})}}],
            'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        base=llm.build_compatible_client('https://api.deepseek.com','offline-only','deepseek-flash',thinking_mode=mode,http_client=http,max_tokens=256)
        monkeypatch.setattr(llm,'_chat_llm',base);monkeypatch.setattr(llm,'_structured_llms',{})
        result=llm.get_structured_llm(Result,json_mode=True).invoke([HumanMessage(content='Return valid JSON with note.')])
    assert result.note=='" SP " → "SP"' and len(requests)==1
    assert requests[0]['response_format']=={'type':'json_object'} and not requests[0].get('tools')
    assert requests[0]['thinking']['type']==('enabled' if mode=='enabled' else 'disabled')


def test_json_mode_request_does_not_change_other_providers(monkeypatch):
    from pydantic import BaseModel
    class Result(BaseModel):
        ok:bool
    calls=[]
    base=SimpleNamespace(openai_api_base='https://example.invalid',with_structured_output=lambda schema,**kwargs:calls.append(kwargs) or object())
    monkeypatch.setattr(llm,'_chat_llm',base);monkeypatch.setattr(llm,'_structured_llms',{})
    assert llm.get_structured_llm(Result,json_mode=True) is llm.get_structured_llm(Result,json_mode=True)
    assert calls==[{}]
