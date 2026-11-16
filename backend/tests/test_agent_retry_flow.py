"""Real graph and DuckDB; replace only the nondeterministic LLM boundary."""
from __future__ import annotations


def test_truncated_sql_is_not_executed_or_retried(monkeypatch):
    from types import SimpleNamespace
    from app.agent import nodes
    from app.agent.graph import decide_after_validator
    calls = []
    def truncated(messages):
        calls.append(1)
        return SimpleNamespace(content='SELECT 1',response_metadata={'finish_reason':'length'})
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=truncated))
    state = {'question':'复杂问题','schema':'table sales'}
    state.update(nodes.write_sql_node(state))
    assert state.get('output_limit_reached') is True
    assert state['sql'] == ''
    state.update(nodes.execute_sql_node(state))
    assert decide_after_validator(state) == 'compose_answer'
    assert '上限' in nodes.compose_answer_node(state)['answer']
    assert calls == [1]

import io
from types import SimpleNamespace

import pytest

from app.agent import graph, nodes
from app.config import settings
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import validate_and_convert


@pytest.fixture
def run_step(monkeypatch, tmp_path):
    manager = DatasetManager()
    manager.get_or_create("retry-test", tmp_path)
    ingested = validate_and_convert(
        "orders.csv", io.BytesIO(b"customer,price\nAlice,100\nBob,200\n"), tmp_path
    )
    manager.add_file("retry-test", ingested)
    monkeypatch.setattr("app.tools.dataset_manager.get_dataset_manager", lambda: manager)
    monkeypatch.setattr(graph, "router_node", lambda state: {"route": "sql"})
    monkeypatch.setattr(settings, "max_agent_retries", 3)

    def run(queries, *, session_id="retry-test", context="", question="Top 5 customers by price"):
        prompts = []

        def invoke(messages):
            if messages[0].content.startswith(nodes.SQL_SYSTEM_PROMPT):
                prompts.append(messages[1].content)
                query = queries[min(len(prompts) - 1, len(queries) - 1)]
                return SimpleNamespace(content=query)
            return SimpleNamespace(content="The analysis could not be completed.")

        monkeypatch.setattr(nodes, "_get_llm", lambda: SimpleNamespace(invoke=invoke))
        final = graph.build_graph().invoke({
            "question": question,
            "schema": "Table orders: customer VARCHAR, price INTEGER",
            "session_id": session_id,
            "session_context": context,
            "previous_attempts": [],
        }, {"recursion_limit": 30})
        return final, prompts

    yield run
    for dataset in manager._sessions.values():
        dataset.close()


@pytest.mark.parametrize("query", [
    "SELECT customer FROM orders WHERE price < 0",
    "SELECT customer, NULL AS decline_rank FROM orders",
])
def test_validation_failure_stops_with_feedback_at_budget(run_step, query):
    final, prompts = run_step([query])
    assert len(prompts) == settings.max_agent_retries
    assert len(final["previous_attempts"]) == settings.max_agent_retries
    assert final["validation_failure"]
    assert final["previous_attempts"][0]["error"] in prompts[1]


def test_execution_failure_is_counted_once_per_attempt(run_step):
    final, prompts = run_step(["SELECT nonexistent FROM orders"])
    assert len(prompts) == settings.max_agent_retries
    assert len(final["previous_attempts"]) == settings.max_agent_retries
    assert final["error"]


def test_legitimate_empty_result_finishes_after_one_sql_without_widening(run_step):
    query = "SELECT customer FROM orders WHERE price < 0"
    final, prompts = run_step([query], question="业务口径：销售额=sum(price)。\n\n当前问题：列出负数订单，无匹配返回空表。")
    assert len(prompts) == 1
    assert final["sql"] == query and final["row_count"] == 0
    assert not final["validation_failure"] and not final["previous_attempts"]
    assert final["answer"].startswith("没有查到符合条件的数据。")
    assert final['analysis_report']['verification_note'] in final['answer']


def test_missing_data_stops_at_budget(run_step):
    final, prompts = run_step(["SELECT customer FROM orders"], session_id="missing")
    assert len(prompts) == settings.max_agent_retries
    assert len(final["previous_attempts"]) == settings.max_agent_retries
    assert "No data available" in final["error"]


def test_corrected_query_clears_failure_and_returns_verified_value(run_step):
    final, prompts = run_step([
        "SELECT customer FROM orders WHERE price < 0",
        "SELECT SUM(price) AS revenue FROM orders",
    ])
    assert len(prompts) == 2
    assert len(final["previous_attempts"]) == 1
    assert final["validation_failure"] == ""
    assert final["error"] == ""
    assert final["rows"] == [{"revenue": 300}]


def test_execution_and_validation_failures_share_one_budget(run_step):
    final, prompts = run_step([
        "SELECT nonexistent FROM orders",
        "SELECT customer FROM orders WHERE price < 0",
        "SELECT customer, NULL AS decline_rank FROM orders",
    ])
    assert len(prompts) == settings.max_agent_retries
    assert len(final["previous_attempts"]) == settings.max_agent_retries
    assert "nonexistent" in prompts[2]
    assert "zero rows" in prompts[2]


def test_correction_keeps_metric_context(run_step):
    rule = "Metric rule: revenue is SUM(price), not COUNT(*)"
    _, prompts = run_step([
        "SELECT customer FROM orders WHERE price < 0",
        "SELECT SUM(price) AS revenue FROM orders",
    ], context=rule)
    assert rule in prompts[1]
