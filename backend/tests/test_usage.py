from datetime import datetime, timezone
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from pydantic import BaseModel

from app import execution
from app.usage import price_usage, reported_usage, summarize_usage

STATUS = {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"}


def test_cache_usage_and_off_peak_price_are_not_double_counted():
    message = AIMessage(content="ok", usage_metadata={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
                        response_metadata={"token_usage": {"prompt_cache_hit_tokens": 800}})
    usage = reported_usage(message)
    record = price_usage(usage, STATUS, datetime(2026, 10, 25, 2, tzinfo=timezone.utc))
    assert record["total_tokens"] == 1200
    assert Decimal(record["cost_cny"]) == Decimal("0.001016")
    assert record["pricing"]["period"] == "off_peak"


def test_weekday_peak_and_unknown_cache_are_explicit_upper_estimates():
    usage = {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200, "cached_input_tokens": None}
    record = price_usage(usage, STATUS, datetime(2026, 10, 26, 2, tzinfo=timezone.utc))
    assert Decimal(record["cost_cny"]) == Decimal("0.00360")
    assert record["pricing"]["period"] == "weekday_peak_upper"
    assert record["pricing"]["cache_usage_known"] is False


def test_unknown_usage_or_provider_is_not_zero_cost():
    assert reported_usage(AIMessage(content="ok")) is None
    usage = reported_usage(AIMessage(content="ok", usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}))
    records = [price_usage(usage, {**STATUS, "base_url": "https://example.com"}), price_usage(None, STATUS)]
    total = summarize_usage(records)
    assert total["total_tokens"] == 5 and total["cost_cny"] is None
    assert total["missing_usage_calls"] == 1 and total["unpriced_calls"] == 1


def test_callback_preserves_usage_through_structured_output_and_parser_failure(monkeypatch):
    from app import model_settings
    monkeypatch.setattr(model_settings, "get_model_status", lambda: STATUS)
    task = execution.AnalysisTask("owner", "session")
    token = execution._current.set(task)
    message = AIMessage(content='{"value": 7}', usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    class Parsed(BaseModel):
        value: int
    model = GenericFakeChatModel(messages=iter([message, message]))
    try:
        parsed = model | (lambda value: Parsed.model_validate_json(value.content))
        assert execution.invoke_model(parsed, []).value == 7
        broken = model | (lambda value: (_ for _ in ()).throw(ValueError("parser failed")))
        with pytest.raises(ValueError):
            execution.invoke_model(broken, [])
        summary = execution.task_evidence()["usage"]
        assert summary["total_tokens"] == 30 and summary["reported_calls"] == 2
        assert task.model_calls == 2
    finally:
        execution._current.reset(token)


def test_usage_remains_recorded_when_stop_arrives_after_response(monkeypatch):
    from app import model_settings
    monkeypatch.setattr(model_settings, "get_model_status", lambda: STATUS)
    task = execution.AnalysisTask("owner", "session")
    token = execution._current.set(task)
    class Model:
        def invoke(self, messages):
            task.cancelled.set()
            return AIMessage(content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    try:
        with pytest.raises(execution.AnalysisCancelled):
            execution.invoke_model(Model(), [])
        assert execution.task_evidence()["usage"]["total_tokens"] == 12
    finally:
        execution._current.reset(token)


def test_usage_and_prices_survive_sqlite_reload_without_credentials(tmp_path):
    from app.session import SessionStore, SQLiteBackend
    backend = SQLiteBackend(tmp_path / "history.sqlite3")
    session = SessionStore(backend=backend).get_or_create(None)
    usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12, "cached_input_tokens": 0}
    evidence = {"usage": summarize_usage([price_usage(usage, STATUS)])}
    backend.save_message(session.session_id, "assistant", "完成", {"evidence": evidence})
    restored = SQLiteBackend(backend.path).get_messages(session.session_id)[0]["metadata"]["evidence"]
    assert restored == evidence
    assert "api_key" not in str(restored)
