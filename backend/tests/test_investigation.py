"""Investigation execution uses real SQL; only the two model boundaries are controlled."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from app.agent import nodes
from app.agent.investigation import InvestigationDecision, run_investigation
from app.config import settings
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import validate_and_convert


@pytest.fixture
def experiment(monkeypatch, tmp_path):
    manager = DatasetManager()
    dataset = manager.get_or_create("experiment", tmp_path)
    manager.add_file("experiment", validate_and_convert(
        "sales.csv", io.BytesIO(
            b"month,quantity,unit_price\n2026-01,100,10\n2026-02,50,10\n"
        ), tmp_path,
    ))
    monkeypatch.setattr("app.tools.dataset_manager.get_dataset_manager", lambda: manager)
    original_execute = dataset.execute
    executions = []

    def execute(sql, *args, **kwargs):
        executions.append(sql)
        return original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(dataset, "execute", execute)

    def run(decisions, queries, budget=6, conversation_context=''):
        requests = []
        written = []

        def decide(messages):
            requests.append(json.loads(messages[-1].content))
            return InvestigationDecision(**decisions[min(len(requests) - 1, len(decisions) - 1)])

        def write(messages):
            if messages[0].content.startswith(nodes.SQL_SYSTEM_PROMPT):
                written.append(messages[-1].content)
                return SimpleNamespace(content=queries[min(len(written) - 1, len(queries) - 1)])
            return SimpleNamespace(content="This analysis step failed.")

        monkeypatch.setattr(settings, "max_investigation_steps", budget)
        monkeypatch.setattr(settings, "max_agent_retries", 3)
        monkeypatch.setattr("app.agent.investigation.get_structured_llm", lambda schema: SimpleNamespace(invoke=decide))
        monkeypatch.setattr(nodes, "_get_llm", lambda: SimpleNamespace(invoke=write))
        result = run_investigation("Investigate sales revenue: quantity * unit_price", "experiment", conversation_context)
        run.written=written
        return result, requests, executions

    yield run
    dataset.close()


def analysis(step_id, question):
    return {"action": "analyze", "step_id": step_id, "question": question, "reason": "Check the evidence"}


FINISH = {"action": "finish", "reason": "The observed results are sufficient"}
TOTAL = "SELECT SUM(quantity * unit_price) AS revenue FROM sales"


def test_successful_results_are_read_by_next_decision(experiment):
    result, requests, executions = experiment([analysis("total", "Total revenue"), FINISH], [TOTAL])
    assert result["status"] == "completed"
    assert result["steps"][0]["rows"] == [{"revenue": 1500}]
    assert requests[1]["steps"][0]["rows"] == [{"revenue": 1500}]
    assert executions == [TOTAL]


def test_sql_step_receives_schema_once_and_keeps_all_previous_rows(experiment):
    result,_,_=experiment([analysis('total','Total revenue'),analysis('monthly','Monthly revenue'),FINISH],
        [TOTAL,'SELECT month,SUM(quantity*unit_price) AS revenue FROM sales GROUP BY month'])
    assert result['status']=='completed'
    nested=experiment.written[1].split('\n\n',1)[1].split('\n\nQuestion:',1)[0]
    context=json.JSONDecoder().raw_decode(nested)[0]
    assert 'schema' not in context
    assert context['steps'][0]['rows']==[{'revenue':1500}]


def test_failed_step_remains_in_memory_before_new_analysis(experiment):
    result, requests, executions = experiment(
        [analysis("bad", "Query missing column"), analysis("total", "Total revenue"), FINISH],
        ["SELECT missing FROM sales"] * 2 + [TOTAL],
    )
    assert result["status"] == "completed"
    assert requests[1]["steps"][0]["status"] == "failed"
    assert len(requests[1]["steps"][0]["attempts"]) == 2
    assert result["steps"][1]["rows"] == [{"revenue": 1500}]
    assert len(executions) == 2


@pytest.mark.parametrize("second", [
    analysis(" TOTAL ", "Another analysis"),
    analysis("another_id", " total REVENUE "),
])
def test_relabeling_a_step_does_not_allow_reexecution(experiment, second):
    result, _, executions = experiment([analysis("total", "Total revenue"), second], [TOTAL])
    assert result["status"] == "blocked"
    assert len(result["steps"]) == 1
    assert executions == [TOTAL]


def test_failed_step_budget_cannot_be_reset_by_outer_loop(experiment):
    result, _, executions = experiment(
        [analysis("bad", "Query missing column"), analysis("bad", "Try a new query")],
        ["SELECT missing FROM sales"],
    )
    assert result["status"] == "blocked"
    assert len(result["steps"][0]["attempts"]) == 2
    assert len(executions) == 1


def test_same_sql_under_new_step_id_is_blocked_before_execution(experiment):
    result, _, executions = experiment(
        [analysis("total", "Total revenue"), analysis("other", "Revenue again")], [TOTAL],
    )
    assert result["status"] == "blocked"
    assert executions == [TOTAL]


def test_whole_investigation_budget_stops_new_steps(experiment):
    result, requests, executions = experiment(
        [analysis("total", "Total revenue"), analysis("count", "Count records"), analysis("third", "Another metric")],
        [TOTAL, "SELECT COUNT(*) AS n FROM sales"], budget=2,
    )
    assert result["status"] == "budget_exhausted"
    assert len(requests) == 2
    assert len(result["steps"]) == 2
    assert len(executions) == 2


def test_malformed_decision_does_not_execute_a_query(experiment):
    result, _, executions = experiment([analysis("", "Total revenue")], [TOTAL])
    assert result["status"] == "planner_error"
    assert executions == []


def test_finish_without_any_evidence_is_inconclusive(experiment):
    result, _, executions = experiment([FINISH], [TOTAL])
    assert result["status"] == "inconclusive"
    assert executions == []


def test_history_only_finish_is_corrected_once_before_current_sql(experiment):
    result,requests,executions=experiment([FINISH,analysis('total','Total revenue'),FINISH],[TOTAL],
                                        conversation_context='历史结论声称 revenue=999，请重新核对。')
    assert result['status']=='completed' and executions==[TOTAL]
    assert result['steps'][0]['rows']==[{'revenue':1500}]
    assert 'current task' in requests[1]['planning_feedback']


def test_history_correction_is_bounded_when_planner_still_refuses_current_evidence(experiment):
    result,requests,executions=experiment([FINISH],[TOTAL],conversation_context='历史分析结果')
    assert result['status']=='inconclusive' and len(requests)==2 and executions==[]


def test_missing_data_is_rejected_before_model_calls():
    with pytest.raises(ValueError, match="uploaded"):
        run_investigation("Investigate sales", "no-dataset")


@pytest.mark.parametrize("action", ["analyze", "finish"])
def test_unresolved_critical_inputs_stop_before_more_queries(experiment, action):
    decision = {"action": action, "reason": "无法确定完整收入，缺少实际单价。",
                "unresolved_inputs": ["sales.unit_price"]}
    if action == "analyze":
        decision.update(step_id="unnecessary_decomposition", question="Break down partial revenue")
    result, requests, executions = experiment([decision], [TOTAL])
    assert result["status"] == "clarify"
    assert "sales.unit_price" in result["reason"] and "请补充" in result["reason"]
    assert executions == [] and len(requests) == 1


def test_clarification_preserves_collected_evidence(experiment):
    decision = {"action": "finish", "reason": "仍缺关键价格口径。", "unresolved_inputs": ["sales.unit_price"]}
    result, requests, executions = experiment([analysis("total", "Observed revenue"), decision], [TOTAL])
    assert result["status"] == "clarify"
    assert result["steps"][0]["rows"] == [{"revenue": 1500}]
    assert executions == [TOTAL] and len(requests) == 2


def test_finish_builds_requested_chart_from_existing_step_without_rerunning_sql(experiment, monkeypatch):
    built = []
    def chart(state):
        built.append(state)
        return {'chart_spec':{'data':[{'type':'bar','x':['all'],'y':[1500]}],'layout':{'title':'总额'}}}
    monkeypatch.setattr('app.agent.investigation.make_chart_node',chart,raising=False)
    result, _, executions = experiment([analysis('total','Total revenue'),
        {'action':'finish','reason':'已取得总额','chart_step_id':'total'}],[TOTAL])
    assert len(built) == 1 and executions == [TOTAL]
    assert result['steps'][0]['chart_spec']['data'][0]['y'] == [1500]


def test_invalid_chart_step_cannot_be_reported_completed(experiment):
    result, _, _ = experiment([analysis('total','Total revenue'),
        {'action':'finish','reason':'完成','chart_step_id':'not-collected'}],[TOTAL])
    assert result['status'] != 'completed'
