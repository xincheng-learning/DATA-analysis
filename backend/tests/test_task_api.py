"""Real graph, SQL, cancellation and saved evidence; only LLM responses are controlled."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from test_local_api import local_app as local_app_fixture
from test_local_api import upload

from app import main
from app.agent import investigation, nodes, router
from app.execution import TaskRegistry

local_app = local_app_fixture


@pytest.fixture
def registry(monkeypatch):
    registry = TaskRegistry()
    monkeypatch.setattr(main, "tasks", registry)
    monkeypatch.setattr(main, "get_chat_llm", lambda: object())
    monkeypatch.setattr(router, "_get_router_llm", lambda: SimpleNamespace(invoke=lambda messages: router.RouteDecision(route="sql", reason="统计销售额", analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    yield registry
    registry.close()


async def wait(client, task):
    for _ in range(200):
        state = (await client.get(f'/tasks/{task["task_id"]}')).json()
        if state["status"] in {"succeeded", "failed", "cancelled"}:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError("task never finished")


async def test_task_event_stream_finishes_and_missing_ids_are_rejected(local_app, registry):
    client, _, _ = local_app
    from app.execution import publish_answer
    def run():
        publish_answer('已经核对的阶段结果。')
        return {'answer':'完成'}
    task=registry.submit('local-user','event-case',run)
    await wait(client,task)
    response=await client.get(f"/tasks/{task['task_id']}/events")
    assert response.status_code==200 and response.headers['content-type'].startswith('text/event-stream')
    assert 'event: task' in response.text and '已经核对的阶段结果' in response.text
    assert '"status": "succeeded"' in response.text
    assert (await client.get('/tasks/not-a-task/events')).status_code==404


async def test_task_real_graph_evidence_survives_restart(local_app, registry, monkeypatch):
    client, restart, _ = local_app
    await upload(client)
    monkeypatch.setattr(nodes, "_get_llm", lambda: SimpleNamespace(invoke=lambda messages: SimpleNamespace(content="SELECT SUM(revenue) AS total FROM sales")))
    response = await client.post("/tasks", json={"question": "销售总额？", "session_id": "local-case"})
    assert response.status_code == 200, response.text
    task = await wait(client, response.json())
    assert task["status"] == "succeeded", task
    result = task["result"]
    assert result["rows"] == [{"total": 300}]
    assert result["evidence"]["model_calls"] == 2
    assert "execute_sql" in [node["node"] for node in result["evidence"]["trace"]]
    restart()
    history = (await client.get("/sessions/local-case")).json()["messages"][-1]["metadata"]
    assert history["rows"] == result["rows"]
    assert history["evidence"] == result["evidence"]
    assert history["data_sources"][0]["source"]["kind"] == "file"


async def test_cancel_in_actual_graph_prevents_sql_execution_and_data_changes(local_app, registry, monkeypatch):
    client, _, _ = local_app
    await upload(client)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def invoke(messages):
        calls.append("model")
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(content="SELECT SUM(revenue) AS total FROM sales")
    monkeypatch.setattr(nodes, "_get_llm", lambda: SimpleNamespace(invoke=invoke))
    task = (await client.post("/tasks", json={"question": "销售总额", "session_id": "local-case"})).json()
    assert await asyncio.to_thread(entered.wait, 3)
    try:
        assert (await client.post("/tasks", json={"question": "再次查询"})).status_code == 409
        assert (await client.delete("/sessions/local-case")).status_code == 409
        assert (await client.post(f'/tasks/{task["task_id"]}/cancel', json={})).json()["status"] == "cancelling"
    finally:
        release.set()
    final = await wait(client, task)
    assert final["status"] == "cancelled", final
    assert len(calls) == 1
    assert "execute_sql" not in [node["node"] for node in final["trace"]]
    assert "已停止" in (await client.get("/sessions/local-case")).json()["messages"][-1]["content"]


async def test_thinking_change_is_blocked_until_inflight_task_finishes_cancelling(local_app,registry,monkeypatch):
    from app import model_settings
    client,_,_=local_app
    config={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'offline-key',
            'thinking_mode':'enabled','reasoning_effort':'low'}
    assert (await client.put('/settings/model',json=config)).status_code==200
    await upload(client)
    entered,release=threading.Event(),threading.Event()
    seen=[]
    def invoke(messages):
        seen.append(model_settings.load_saved_settings().reasoning_effort)
        entered.set()
        assert release.wait(5)
        seen.append(model_settings.load_saved_settings().reasoning_effort)
        return SimpleNamespace(content='SELECT SUM(revenue) AS total FROM sales')
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=invoke))
    task=(await client.post('/tasks',json={'question':'核对销售额','session_id':'local-case'})).json()
    assert await asyncio.to_thread(entered.wait,3)
    changed={**config,'api_key':None,'reasoning_effort':'high'}
    try:
        assert (await client.put('/settings/model',json=changed)).status_code==409
        await client.post(f"/tasks/{task['task_id']}/cancel",json={})
        assert (await client.put('/settings/model',json=changed)).status_code==409
    finally:
        release.set()
    assert (await wait(client,task))['status']=='cancelled'
    assert seen==['low','low']
    assert (await client.put('/settings/model',json=changed)).status_code==200
    assert model_settings.load_saved_settings().reasoning_effort=='high'


async def test_web_investigation_uses_existing_controller(local_app, registry, monkeypatch):
    from app import analysis_report
    monkeypatch.setattr(analysis_report, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=lambda messages: (_ for _ in ()).throw(RuntimeError('offline explanation'))))
    client, _, _ = local_app
    await upload(client)
    decisions = iter([
        investigation.InvestigationDecision(action="analyze", step_id="total", question="销售总额", reason="先核对总量"),
        investigation.InvestigationDecision(action="finish", reason="已核对总额为300；区域数据缺失，不能解释下降原因。"),
    ])
    monkeypatch.setattr(investigation, "get_structured_llm", lambda schema: SimpleNamespace(invoke=lambda messages: next(decisions)))
    monkeypatch.setattr(nodes, "_get_llm", lambda: SimpleNamespace(invoke=lambda messages: SimpleNamespace(content="SELECT SUM(revenue) AS total FROM sales")))
    task = (await client.post("/tasks", json={"question": "调查销售额", "session_id": "local-case", "mode": "investigate"})).json()
    final = await wait(client, task)
    assert final["status"] == "succeeded", final
    result = final["result"]
    assert result["investigation"]["steps"][0]["rows"] == [{"total": 300}]
    assert "区域数据缺失" in result["investigation"]["planner_summary"]
    assert "区域数据缺失" not in result["answer"]
    assert "300" in result["answer"]
    metadata = (await client.get("/sessions/local-case")).json()["messages"][-1]["metadata"]
    assert metadata["investigation"] == result["investigation"]


async def test_ordinary_question_can_use_the_bounded_investigation_and_report(local_app, registry, monkeypatch):
    from app import analysis_report
    client, _, _ = local_app
    await upload(client)
    monkeypatch.setattr(router, '_get_router_llm', lambda: SimpleNamespace(invoke=lambda messages: router.RouteDecision(route='investigate', reason='拆解经营变化', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    decisions = iter([
        investigation.InvestigationDecision(action='analyze', step_id='total', question='核对金额', reason='先核对总量'),
        investigation.InvestigationDecision(action='finish', reason='已取得当前金额，尚无可比期间'),
    ])
    monkeypatch.setattr(investigation, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=lambda messages: next(decisions)))
    monkeypatch.setattr(nodes, '_get_llm', lambda: SimpleNamespace(invoke=lambda messages: SimpleNamespace(content='SELECT SUM(revenue) AS total FROM sales')))
    plan = analysis_report.ReportPlan(findings=[{'text': '已取得当前金额，但缺少可比期间，不能判断变化来源。', 'evidence_ids': ['S1.R1']}])
    monkeypatch.setattr(analysis_report, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=lambda messages: plan))
    task = (await client.post('/tasks', json={'question': '经营金额为什么变化，帮我查查', 'session_id': 'local-case'})).json()
    final = await wait(client, task)
    assert final['status'] == 'succeeded', final
    result = final['result']
    assert result['route'] == 'investigate'
    assert result['investigation']['synthesis']['status'] == 'generated'
    assert 'S1.R1' in result['answer'] and '300' in result['answer']
    assert result['metric_context']['version'].startswith('commerce-definitions/')
    metadata = (await client.get('/sessions/local-case')).json()['messages'][-1]['metadata']
    assert metadata['metric_context'] == result['metric_context']
