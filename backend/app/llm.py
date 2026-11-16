"""Single source of LLM clients.

All agent code goes through this module so swapping providers is one config
change, not a sweep through the codebase. Two helpers:

  - get_chat_llm()                 plain chat for SQL writing, etc.
  - get_structured_llm(SchemaCls)  same model with .with_structured_output()

Provider selection is driven by ``settings.llm_provider``:
  "groq"   — langchain_groq.ChatGroq (recommended; generous free tier)
  "gemini" — langchain_google_genai.ChatGoogleGenerativeAI
"""
from __future__ import annotations

import logging
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel

from app import model_settings
from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMConfigurationError(RuntimeError):
    """The selected provider has no usable local configuration."""


def build_compatible_client(base_url: str, api_key: str, model: str, *, thinking_mode="auto", reasoning_effort="low", **kwargs) -> Any:
    import httpx
    from langchain_openai import ChatOpenAI

    class DeepSeekChatOpenAI(ChatOpenAI):
        def with_structured_output(self, *args, **options):
            runnable = super().with_structured_output(*args, **options)
            if thinking_mode == "enabled" and not options.get("include_raw"):
                from langchain_core.runnables import RunnableLambda

                def require_result(result):
                    if result is None:
                        raise ValueError("已开启思考，但模型未返回所需工具结果；未自动关闭思考或补发请求。")
                    return result
                return runnable | RunnableLambda(require_result)
            return runnable

        def _get_request_payload(self, input_, *, stop=None, **options):
            payload = super()._get_request_payload(input_, stop=stop, **options)
            if "max_completion_tokens" in payload:
                payload["max_tokens"] = payload.pop("max_completion_tokens")
            choice = payload.get("tool_choice")
            forced_tool = isinstance(choice, dict) or choice == "required"
            if thinking_mode == "enabled":
                payload["extra_body"] = {**(payload.get("extra_body") or {}), "thinking": {"type": "enabled"}}
                payload["reasoning_effort"] = reasoning_effort
                if forced_tool:
                    payload["tool_choice"] = "auto"
            elif thinking_mode == "disabled" or forced_tool or payload.get('response_format',{}).get('type') in {'json_object','json_schema'}:
                # Auto preserves the old compatibility policy; explicit modes apply to every request.
                payload["extra_body"] = {**(payload.get("extra_body") or {}), "thinking": {"type": "disabled"}}
            return payload

    client_class = DeepSeekChatOpenAI if urlsplit(base_url).hostname == "api.deepseek.com" else ChatOpenAI
    if client_class is DeepSeekChatOpenAI:
        kwargs.setdefault('max_tokens',16384)
        kwargs.setdefault('stream_usage', True)
    transport = kwargs.pop("http_client", None) or httpx.Client(follow_redirects=False)
    return client_class(
        base_url=base_url, api_key=api_key, model=model, temperature=0.0,
        timeout=90.0, max_retries=0,
        http_client=transport, **kwargs,
    )


def check_model_connection(config: model_settings.ModelSettingsInput) -> None:
    from langchain_core.messages import HumanMessage

    class ConnectionProbe(BaseModel):
        ready: bool

    client = build_compatible_client(
        config.base_url, config.api_key.get_secret_value(), config.model,
        thinking_mode=config.thinking_mode, reasoning_effort=config.reasoning_effort,
        max_tokens=1024 if config.thinking_mode == "enabled" else 64,
    )
    try:
        result = client.with_structured_output(ConnectionProbe).invoke([
            HumanMessage(content="Connection check only. Return ready=true using the provided tool."),
        ])
        if not isinstance(result, ConnectionProbe) or not result.ready:
            raise ValueError("structured response was not returned")
    finally:
        client.http_client.close()


def _build_chat_llm() -> Any:
    """Construct the underlying chat LLM for the configured provider."""
    if getattr(settings, "app_mode", "cloud") == "local":
        saved = model_settings.load_saved_settings()
        if saved:
            return build_compatible_client(saved.base_url, saved.api_key.get_secret_value(), saved.model,
                                           thinking_mode=saved.thinking_mode, reasoning_effort=saved.reasoning_effort)
    provider = settings.llm_provider.lower()

    if provider == "openai_compatible":
        if not all((settings.llm_base_url, settings.llm_api_key, settings.llm_model)):
            raise LLMConfigurationError("Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL for openai_compatible.")
        return build_compatible_client(settings.llm_base_url, settings.llm_api_key, settings.llm_model)

    if provider == "groq":
        if not settings.groq_api_key:
            raise LLMConfigurationError(
                "LLM_PROVIDER=groq but GROQ_API_KEY is not set in .env. "
                "Get a key at https://console.groq.com."
            )
        from langchain_groq import ChatGroq

        logger.info("Using Groq model: %s", settings.groq_model)
        return ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=0.0,
            max_retries=0,
            timeout=30.0,
        )

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise LLMConfigurationError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set in .env. "
                "Get a key at https://aistudio.google.com."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        logger.info("Using Gemini model: %s", settings.primary_model)
        return ChatGoogleGenerativeAI(
            model=settings.primary_model,
            google_api_key=settings.gemini_api_key,
            temperature=0.0,
            max_retries=0,
            timeout=30.0,
        )

    raise LLMConfigurationError(
        f"Unknown LLM_PROVIDER '{settings.llm_provider}'. "
        "Expected 'groq', 'gemini' or 'openai_compatible'."
    )


# --- Cached singletons ------------------------------------------------------

_chat_llm: Any = None
_structured_llms: dict[tuple[type[BaseModel], bool], Any] = {}


def get_chat_llm() -> Any:
    """Plain chat LLM (free-form text output)."""
    global _chat_llm
    with model_settings.settings_lock:
        if _chat_llm is None:
            _chat_llm = _build_chat_llm()
        return _chat_llm


def get_structured_llm(schema: type[T], *, json_mode: bool = False) -> Any:
    """Chat LLM that returns Pydantic model instances of ``schema``.

    Cached per-schema. Uses langchain's with_structured_output so both
    Gemini and Groq go through the same interface.
    """
    with model_settings.settings_lock:
        key=(schema,json_mode)
        if key not in _structured_llms:
            base = get_chat_llm()
            options={'method':'json_mode'} if json_mode and urlsplit(getattr(base,'openai_api_base','') or '').hostname=='api.deepseek.com' else {}
            _structured_llms[key] = base.with_structured_output(schema,**options)
        return _structured_llms[key]


def reset_cache() -> None:
    """Drop cached clients. Useful in tests or after a config change."""
    global _chat_llm
    with model_settings.settings_lock:
        _chat_llm = None
        _structured_llms.clear()
