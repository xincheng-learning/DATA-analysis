from __future__ import annotations

import threading
import time

import pytest

from app.execution import TaskRegistry, checkpoint, invoke_model


def test_sql_trace_is_visible_during_execution_and_retains_each_retry():
    from app.execution import controlled_node
    registry = TaskRegistry()
    started, release = threading.Event(), threading.Event()
    def query(state):
        started.set()
        assert release.wait(2)
        return {"row_count": 2, "error": ""}
    def work():
        controlled_node("execute_sql", query)({"sql": "SELECT 1", "question": "测试"})
        controlled_node("execute_sql", lambda state: {"error": "failed", "row_count": 0})({"sql": "SELECT 1"})
    item = registry.submit("owner", "session", work)
    try:
        assert started.wait(2)
        trace = registry.read(item["task_id"], "owner")["trace"]
        assert trace[0]["sql"] == "SELECT 1" and trace[0]["status"] == "running"
        release.set()
        result = completed(registry, item["task_id"])
        assert [step["status"] for step in result["trace"]] == ["succeeded", "failed"]
        assert [step["sql"] for step in result["trace"]] == ["SELECT 1", "SELECT 1"]
        assert result["trace"][0]["row_count"] == 2
        assert trace[0]["status"] == "running"
    finally:
        release.set()
        registry.close()


def test_sql_exception_is_recorded_without_exception_secrets():
    from app.execution import controlled_node
    registry = TaskRegistry()
    try:
        item = registry.submit("owner", "session", lambda: controlled_node("execute_sql", lambda state: (
            _ for _ in ()).throw(RuntimeError("secret-key")))({"sql": "SELECT broken"}))
        result = completed(registry, item["task_id"])
        assert result["trace"][0]["status"] == "failed"
        assert result["trace"][0]["sql"] == "SELECT broken"
        assert "secret-key" not in str(result)
    finally:
        registry.close()


def completed(registry, task_id):
    for _ in range(200):
        item = registry.read(task_id, "owner")
        if item["status"] in {"succeeded", "failed", "cancelled"}:
            return item
        time.sleep(0.005)
    raise AssertionError("Task did not finish")


def test_cancel_during_model_call_stops_the_next_operation():
    registry = TaskRegistry()
    started, release = threading.Event(), threading.Event()
    calls = []
    class Model:
        def invoke(self, messages):
            calls.append("model")
            started.set()
            assert release.wait(2)
            return "response"
    def work():
        invoke_model(Model(), [])
        calls.append("should-not-run")
        return {}
    item = registry.submit("owner", "session", work)
    assert started.wait(2)
    assert registry.cancel(item["task_id"], "owner")["status"] == "cancelling"
    release.set()
    assert completed(registry, item["task_id"])["status"] == "cancelled"
    assert calls == ["model"]
    registry.close()


def test_only_one_active_task_and_no_cross_owner_access():
    registry = TaskRegistry()
    release = threading.Event()
    item = registry.submit("owner", "session", lambda: release.wait(2))
    with pytest.raises(ValueError, match="进行"):
        registry.submit("owner", "other", lambda: {})
    with pytest.raises(KeyError):
        registry.read(item["task_id"], "stranger")
    with pytest.raises(KeyError):
        registry.cancel(item["task_id"], "stranger")
    release.set()
    assert completed(registry, item["task_id"])["status"] == "succeeded"
    registry.close()


def test_model_budget_is_global_to_the_task_and_errors_are_safe():
    registry = TaskRegistry()
    calls = []
    class Model:
        def invoke(self, messages):
            calls.append(1)
            return "ok"
    def work():
        checkpoint("planning")
        invoke_model(Model(), [])
        checkpoint("write_sql")
        invoke_model(Model(), [])
    item = registry.submit("owner", "session", work, max_model_calls=1)
    result = completed(registry, item["task_id"])
    assert result["status"] == "failed" and "预算" in result["error"]
    assert calls == [1] and result["model_calls"] == 1
    failed = registry.submit("owner", "session", lambda: (_ for _ in ()).throw(RuntimeError("secret-key")))
    result = completed(registry, failed["task_id"])
    assert result["status"] == "failed" and "secret-key" not in str(result)
    registry.close()
