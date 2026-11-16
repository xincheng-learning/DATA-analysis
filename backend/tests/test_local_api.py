"""Real local HTTP, disk persistence and graph; no cloud or real LLM calls."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import llm, main
from app.agent import graph, nodes, router
from app.config import settings
from app.session import SessionStore, SQLiteBackend
from app.tools.dataset_manager import DatasetManager


@pytest.fixture
async def local_app(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_mode", "local")
    monkeypatch.setattr(settings, "session_backend", "sqlite")
    monkeypatch.setattr(settings, "local_data_dir", tmp_path)
    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_base_url", None)
    monkeypatch.setattr(settings, "llm_api_key", None)
    monkeypatch.setattr(settings, "llm_model", None)
    monkeypatch.setattr(settings, "cors_allowed_origins", "http://localhost:3000,http://127.0.0.1:3000")
    monkeypatch.setattr(main.limiter, "enabled", False)
    state = SimpleNamespace(manager=DatasetManager(), store=SessionStore(
        backend=SQLiteBackend(tmp_path / "datapilot.sqlite3"),
    ))
    monkeypatch.setattr(main, "get_session_store", lambda: state.store)
    monkeypatch.setattr(graph, "get_session_store", lambda: state.store)
    monkeypatch.setattr(main, "get_dataset_manager", lambda: state.manager)
    monkeypatch.setattr("app.tools.dataset_manager.get_dataset_manager", lambda: state.manager)
    monkeypatch.setattr(graph, "_graph", None)

    def no_cloud():
        raise AssertionError("Local mode must not contact Supabase")

    monkeypatch.setattr("app.tools.file_storage._get_storage", no_cloud)

    def restart():
        for dataset in state.manager._sessions.values():
            dataset.close()
        state.manager = DatasetManager()
        state.store = SessionStore(backend=SQLiteBackend(tmp_path / "datapilot.sqlite3"))

    llm.reset_cache()
    transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        yield client, restart, tmp_path
    restart()
    llm.reset_cache()


async def upload(client, sid="local-case"):
    response = await client.post(f"/sessions/{sid}/upload", files={
        "files": ("sales.csv", b"month,revenue\n2026-01,100\n2026-02,200\n", "text/csv"),
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


async def test_upload_and_history_survive_backend_and_dataset_restart(local_app):
    client, restart, root = local_app
    await upload(client)
    assert (root / "uploads" / "local-case" / "sales.parquet").exists()
    restart()
    sessions = (await client.get("/sessions")).json()
    assert sessions[0]["session_id"] == "local-case"
    assert "sales.csv" in sessions[0]["title"]
    detail = (await client.get("/sessions/local-case")).json()
    assert detail["messages"][0]["content"] == "📎 已上传：sales.csv"
    tables = (await client.get("/sessions/local-case/tables")).json()
    assert tables["tables"][0]["rows"] == 2
    assert "Table: sales" in (await client.get("/schema")).json()["schema"]


async def test_upload_after_restart_preserves_existing_tables(local_app):
    client, restart, root = local_app
    await upload(client)
    restart()
    response = await client.post("/sessions/local-case/upload", files={
        "files": ("sales.csv", b"month,revenue\n2026-03,999\n", "text/csv"),
    })
    assert response.status_code == 200
    tables = (await client.get("/sessions/local-case/tables")).json()["tables"]
    assert {table["table_name"]: table["rows"] for table in tables} == {"sales": 2, "sales_2": 1}
    assert (root / "uploads" / "local-case" / "sales.parquet").exists()
    assert (root / "uploads" / "local-case" / "sales_2.parquet").exists()


async def test_local_rename_and_delete_keep_other_session_data(local_app):
    client, restart, root = local_app
    await upload(client)
    await upload(client, "keep-case")
    assert (await client.patch("/sessions/local-case", json={"title": "本地实验"})).status_code == 200
    restart()
    assert (await client.get("/sessions/local-case")).json()["title"] == "本地实验"
    # Open DuckDB before deletion to catch Windows file-handle problems.
    assert (await client.get("/schema")).status_code == 200
    assert (await client.delete("/sessions/local-case")).json()["status"] == "deleted"
    restart()
    assert not (root / "uploads" / "local-case").exists()
    assert (root / "uploads" / "keep-case" / "sales.parquet").exists()
    assert (await client.get("/sessions/local-case")).status_code == 404
    assert len((await client.get("/sessions")).json()) == 1


@pytest.mark.parametrize('failure',[False,True])
async def test_python_receipt_survives_response_restart_and_context(local_app,monkeypatch,failure):
    client,restart,_=local_app
    from app.tools import python_tool
    from app.context_memory import project_messages
    code="result = {'count': 2, 'mean': 150.0}"
    output="{'count': 2, 'mean': 150.0}"
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda _:router.RouteDecision(route='python',reason='Python统计', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=lambda _:SimpleNamespace(content=code)))
    def execute(actual,*args,**kwargs):
        assert actual==code
        return SimpleNamespace(ok=not failure,error='Docker 不可用' if failure else None,output=None if failure else output)
    monkeypatch.setattr(python_tool,'execute_python',execute)
    await upload(client)
    response=await client.post('/ask',json={'question':'请用Python统计','session_id':'local-case'})
    assert response.status_code==200,response.text
    result=response.json()
    assert result.get('python_code')==code
    assert result.get('python_output')==('' if failure else output)
    assert bool(result.get('error'))==failure
    assert result['python_attempts'][0]['code']==code
    assert bool(result['python_attempts'][0]['error'])==failure
    restart()
    message=(await client.get('/sessions/local-case')).json()['messages'][-1]
    assert message['metadata']['python_code']==code
    assert message['metadata']['python_output']==('' if failure else output)
    assert message['metadata']['python_attempts']==result['python_attempts']
    projected=project_messages([message])[0]['evidence']
    assert projected['python_code']==code and projected['python_output']==('' if failure else output)
    assert projected['python_attempts']==result['python_attempts']


async def test_declared_calculation_and_constraint_receipts_survive_restart(local_app, monkeypatch):
    from app.context_memory import project_messages
    client, restart, _ = local_app
    question = '汇总销售额，返回一行revenue，保持原表与完整精度。'
    plan = {'source_quotes': ['汇总销售额'], 'row_count': 1, 'ordered_columns': True,
        'stages': [{'name': 'total', 'source': 'sales', 'select': [
            {'name': 'revenue', 'op': 'sum', 'field': 'sales.revenue'}]}]}
    parts = [{'requirement': '汇总销售额', 'criterion': {'answer_type': 'value', 'result_columns': ['revenue']}}]
    for requirement, kind in [('一行revenue', 'output_shape'), ('保持原表', 'preserve_source'), ('完整精度', 'precision')]:
        parts.append({'requirement': requirement, 'criterion': {'answer_type': 'constraint', 'subject': requirement, 'constraint_kind': kind}})
    decision = router.RouteDecision(route='sql', reason='已声明汇总口径', analysis_brief={
        'goal': question, 'parts': parts, 'calculation_plan': plan})
    monkeypatch.setattr(main, 'get_chat_llm', lambda: object())
    monkeypatch.setattr(router, '_get_router_llm', lambda: SimpleNamespace(invoke=lambda _: decision))
    monkeypatch.setattr(nodes, '_get_llm', lambda: SimpleNamespace(invoke=lambda _: SimpleNamespace(content='SELECT SUM(revenue) AS revenue FROM sales')))
    await upload(client)
    response = await client.post('/ask', json={'question': question, 'session_id': 'local-case'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['rows'] == [{'revenue': 300}]
    assert all(c['status'] == 'answered' for c in result['analysis_process']['coverage'])
    assert any(c['id'] == 'calculation_contract' and c['status'] == 'passed' for c in result['checks'])
    assert len([c for c in result['checks'] if c['id'] == 'execution_constraint']) == 3
    restart()
    message = (await client.get('/sessions/local-case')).json()['messages'][-1]
    assert message['metadata']['checks'] == result['checks']
    assert message['metadata']['analysis_brief']['calculation_plan'] == result['analysis_brief']['calculation_plan']
    projection = project_messages([message])[0]['evidence']
    assert projection['analysis_brief']['calculation_plan'] == result['analysis_brief']['calculation_plan']


async def test_missing_model_reports_configuration_instead_of_fake_answer(local_app):
    client, _, _ = local_app
    response = await client.post("/ask", json={"question": "销售额是多少？"})
    assert response.status_code == 503
    assert "模型设置" in response.json()["detail"]
    assert "API 密钥" in response.json()["detail"]
    assert (await client.get("/sessions")).json() == []


async def test_local_query_uses_real_graph_and_saves_answer(local_app, monkeypatch):
    client, restart, _ = local_app
    monkeypatch.setattr(main, "get_chat_llm", lambda: object())
    monkeypatch.setattr(router, "_get_router_llm", lambda: SimpleNamespace(
        invoke=lambda messages: router.RouteDecision(route="sql", reason="Sum revenue", analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]}),
    ))
    monkeypatch.setattr(nodes, "_get_llm", lambda: SimpleNamespace(
        invoke=lambda messages: SimpleNamespace(content="SELECT SUM(revenue) AS total FROM sales"),
    ))
    await upload(client)
    restart()
    response = await client.post("/ask", json={"question": "Total revenue", "session_id": "local-case"})
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"total": 300}]
    restart()
    detail = (await client.get("/sessions/local-case")).json()
    assert detail["messages"][-1]["metadata"]["route"] == "sql"
    assert "300" in detail["messages"][-1]["content"]


async def test_data_overview_saves_real_partial_previews_without_inventing_a_sql_run(local_app,monkeypatch):
    client,restart,_=local_app
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:
        router.RouteDecision(route='chat',reason='查看表与缺失字段',show_data_preview=True, analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    monkeypatch.setattr(nodes,'get_structured_llm',lambda schema:SimpleNamespace(invoke=lambda messages:nodes.OverviewExplanation(impacts={'sales.amount':'金额未知，相关记录不能直接按零计入总额。'})))
    response=await client.post('/sessions/overview/upload',files={'files':('sales.csv',b'item,amount,region\na,10,East\nb,,West\nc,30,East\nd,40,West\n','text/csv')})
    assert response.status_code==200
    response=await client.post('/ask',json={'question':'介绍有哪些表、哪些字段缺失，给我看样例','session_id':'overview'})
    assert response.status_code==200,response.text
    result=response.json()
    preview=result['table_previews'][0]
    assert '当前导入了 1 张表' in result['answer']
    assert result['sql'] is None and not result['chart_spec']
    assert preview['rows']==4 and preview['null_counts']['amount']==1
    assert result['data_dictionary'][0]['fields'][1]['missing_percent']==25
    assert len(preview['preview'])==3 and preview['preview'][1]['amount'] is None
    restart()
    await client.post('/sessions/overview/upload',files={'files':('other.csv',b'name\nx\n','text/csv')})
    history=(await client.get('/sessions/overview')).json()['messages']
    saved=next(message for message in history if message['role']=='assistant')
    assert saved['metadata']['table_previews']==result['table_previews']
    assert saved['metadata']['data_dictionary']==result['data_dictionary']


@pytest.mark.parametrize("peer,host,origin", [
    ("198.51.100.2", "localhost", None),
    ("127.0.0.1", "untrusted.invalid", None),
    ("127.0.0.1", "localhost", "https://untrusted.invalid"),
    ("127.0.0.1", "localhost", "null"),
])
async def test_local_mode_rejects_nonlocal_peers_hosts_and_origins(local_app, peer, host, origin):
    transport = httpx.ASGITransport(app=main.app, client=(peer, 12345))
    headers = {"Origin": origin} if origin else {}
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{host}:8000") as client:
        assert (await client.get("/sessions", headers=headers)).status_code == 403


async def test_local_upload_cannot_escape_data_directory(local_app):
    client, _, root = local_app
    response = await client.post("/sessions/..%5Coutside/upload", files={
        "files": ("sales.csv", b"x\n1\n", "text/csv"),
    })
    assert response.status_code == 422
    assert not (root / "outside").exists()
    assert (await client.get("/sessions")).json() == []


async def test_failed_file_cleanup_keeps_local_history_for_retry(local_app, monkeypatch):
    client, _, root = local_app
    await upload(client)

    def cannot_delete(session_id):
        raise OSError("File is in use")

    monkeypatch.setattr("app.tools.file_storage.delete_session_files", cannot_delete)
    response = await client.delete("/sessions/local-case")
    assert response.status_code == 500
    assert (await client.get("/sessions/local-case")).status_code == 200
    assert (root / "uploads" / "local-case" / "sales.parquet").exists()


async def test_resolved_session_directory_cannot_target_all_uploads(local_app, monkeypatch):
    client, _, root = local_app
    await upload(client)
    await upload(client, "keep-case")
    original_resolve = Path.resolve
    alias = root / "uploads" / "local-case"

    def resolve(path, *args, **kwargs):
        # Inject a junction resolving to its parent without needing Windows symlink privileges.
        return root / "uploads" if path == alias else original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    response = await client.delete("/sessions/local-case")
    assert response.status_code == 422
    assert (root / "uploads" / "keep-case" / "sales.parquet").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI persistence requires Windows")
async def test_model_settings_save_read_and_hot_switch_without_key_echo(local_app):
    client, restart, root = local_app
    assert not (await client.get("/settings/model")).json()["configured"]
    body = {"base_url": "https://example.invalid/v1", "model": "model-one", "api_key": "offline-secret-one"}
    result = await client.put("/settings/model", json=body)
    assert result.status_code == 200, result.text
    assert "offline-secret-one" not in result.text
    assert result.headers["cache-control"] == "no-store"
    assert b"offline-secret-one" not in (root / "model-settings.bin").read_bytes()
    first = llm.get_chat_llm()
    assert first.model_name == "model-one"
    assert nodes._get_llm() is first
    old_router = router._get_router_llm()
    from app.tools import viz
    old_chart = viz._get_chart_llm()
    changed = await client.put("/settings/model", json={**body, "model": "model-two", "api_key": None})
    assert changed.status_code == 200
    second = llm.get_chat_llm()
    assert second is not first and second.model_name == "model-two"
    assert nodes._get_llm() is second
    assert router._get_router_llm() is not old_router
    assert viz._get_chart_llm() is not old_chart
    llm.reset_cache()
    restart()
    status = await client.get("/settings/model")
    assert status.json()["configured"] and status.json()["model"] == "model-two"
    assert "offline-secret-one" not in status.text


async def test_model_settings_errors_never_echo_input_secrets(local_app):
    client, _, root = local_app
    response = await client.put("/settings/model", json={
        "base_url": "http://untrusted.invalid/v1", "model": "m", "api_key": "do-not-echo-secret",
    })
    assert response.status_code == 422
    assert "do-not-echo-secret" not in response.text
    assert "input" not in response.text
    assert not (root / "model-settings.bin").exists()
    assert (await client.put("/settings/model", content='{}', headers={"Content-Type": "text/plain"})).status_code == 415


async def test_model_settings_cannot_change_during_another_tabs_running_task(local_app,monkeypatch):
    client,_,_=local_app
    from app import main,model_settings
    body={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'offline-secret-one',
          'thinking_mode':'enabled','reasoning_effort':'low'}
    assert (await client.put('/settings/model',json=body)).status_code==200
    monkeypatch.setattr(main.tasks,'has_active_tasks',lambda:True)
    response=await client.put('/settings/model',json={**body,'api_key':None,'reasoning_effort':'high'})
    assert response.status_code==409
    assert model_settings.load_saved_settings().reasoning_effort=='low'
    assert 'offline-secret-one' not in response.text


async def test_thinking_mode_save_read_cache_switch_and_connection_test_agree(local_app, monkeypatch):
    from test_deepseek_compatibility import tool_response

    client, restart, _ = local_app
    requests, transports = [], []
    build = llm.build_compatible_client
    def create(*args, **kwargs):
        def respond(request):
            import json
            payload = json.loads(request.content)
            requests.append(payload)
            if payload.get('response_format') == {'type': 'json_object'}:
                return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 0, 'model': 'deepseek-flash',
                    'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({
                        'route': 'sql', 'reason': '离线验证', 'analysis_brief': {'goal': '统计', 'requirements': ['统计'],
                            'acceptance': [{'requirement_index': 0, 'answer_type': 'value', 'result_columns': ['n']}]}})}}]})
            name = payload["tools"][0]["function"]["name"]
            return tool_response(name, {"ready": True} if name == "ConnectionProbe" else {"route": "sql", "reason": "离线验证",'analysis_brief':{'goal':'统计','requirements':['统计'],'acceptance':[{'requirement_index':0,'answer_type':'value','result_columns':['n']}]}})
        transport = httpx.Client(transport=httpx.MockTransport(respond))
        transports.append(transport)
        return build(*args, **kwargs, http_client=transport)
    monkeypatch.setattr(llm, "build_compatible_client", create)
    try:
        body = {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": "offline-thinking-secret",
                "thinking_mode": "enabled", "reasoning_effort": "high"}
        assert (await client.put("/settings/model", json=body)).status_code == 200
        first = router._get_router_llm()
        assert first.invoke("路由检查").route == "sql"
        assert requests[-1]["thinking"]["type"] == "enabled" and requests[-1]["reasoning_effort"] == "high"
        assert requests[-1]['response_format'] == {'type': 'json_object'} and not requests[-1].get('tools')
        checked = await client.post("/settings/model/test", json={**body, "api_key": None})
        assert checked.status_code == 200
        assert requests[-1]["max_tokens"] == 1024 and requests[-1]["thinking"]["type"] == "enabled"
        changed = await client.put("/settings/model", json={**body, "api_key": None, "thinking_mode": "disabled"})
        assert changed.status_code == 200
        second = router._get_router_llm()
        assert second is not first and second.invoke("路由检查").route == "sql"
        assert requests[-1]["thinking"]["type"] == "disabled" and requests[-1]['response_format'] == {'type': 'json_object'}
        restart()
        status = await client.get("/settings/model")
        assert status.json()["thinking_mode"] == "disabled" and status.json()["thinking_supported"]
        assert "offline-thinking-secret" not in status.text
        assert len(requests) == 3
    finally:
        for transport in transports:
            transport.close()


async def test_connection_check_does_not_save_or_echo_provider_errors(local_app, monkeypatch):
    client, _, root = local_app
    calls = []
    def check(config):
        calls.append(config)
    monkeypatch.setattr(main, "check_model_connection", check)
    body = {"base_url": "https://example.invalid/v1", "model": "model", "api_key": "offline-connection-secret"}
    success = await client.post("/settings/model/test", json=body)
    assert success.status_code == 200 and len(calls) == 1
    assert not (root / "model-settings.bin").exists()
    def rejected(config):
        raise RuntimeError("provider echoed offline-connection-secret")
    monkeypatch.setattr(main, "check_model_connection", rejected)
    failure = await client.post("/settings/model/test", json=body)
    assert failure.status_code == 502
    assert "offline-connection-secret" not in failure.text


@pytest.mark.parametrize("status, expected", [(400, "HTTP 400"), (422, "HTTP 422"), (402, "余额不足"), (None, "工具调用格式")])
async def test_connection_errors_are_specific_without_echoing_credentials(local_app, monkeypatch, caplog, status, expected):
    client, _, _ = local_app
    secret = "offline-connection-secret"
    def rejected(config):
        if status is None:
            raise ValueError(secret)
        error = RuntimeError(secret)
        error.status_code = status
        raise error
    monkeypatch.setattr(main, "check_model_connection", rejected)
    response = await client.post("/settings/model/test", json={
        "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": secret,
    })
    assert response.status_code == 502
    assert expected in response.json()["detail"]
    assert secret not in response.text and secret not in caplog.text


async def test_model_settings_are_local_only(local_app, monkeypatch):
    client, _, _ = local_app
    monkeypatch.setattr(settings, "app_mode", "cloud")
    monkeypatch.setattr("app.auth._validate_token", lambda token: "offline-user")
    assert (await client.get("/settings/model", headers={"Authorization": "Bearer offline"})).status_code == 403


async def test_foreign_websites_cannot_read_write_or_test_model_keys(local_app):
    client, _, root = local_app
    body = {"base_url": "https://example.invalid/v1", "model": "offline", "api_key": "offline-only-secret"}
    for method, path in [("GET", "/settings/model"), ("PUT", "/settings/model"), ("POST", "/settings/model/test")]:
        response = await client.request(method, path, headers={"Origin": "https://untrusted.invalid"}, json=body)
        assert response.status_code == 403
    assert not (root / "model-settings.bin").exists()


async def test_empty_query_delivery_and_coverage_survive_history_restart(local_app,monkeypatch):
    from app import analysis_pipeline
    client,restart,_=local_app
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    await upload(client,'empty-contract')
    decision=router.RouteDecision(route='sql',reason='返回原条件下的空表',analysis_brief={
        'goal':'列出revenue<0的行','requirements':['完整结果表'],
        'acceptance':[{'requirement_index':0,'answer_type':'table','result_columns':['month','revenue']}]})
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=lambda messages:SimpleNamespace(content='SELECT month,revenue FROM sales WHERE revenue<0')))
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *args:pytest.fail('empty table must not add report call'))
    response=await client.post('/ask',json={'session_id':'empty-contract','question':'列出revenue<0的行'})
    assert response.status_code==200,response.text
    final=response.json()
    assert final['rows']==[] and final['columns']==['month','revenue'] and final['row_count']==0
    assert final['analysis_process']['coverage'][0]['status']=='answered'
    assert final['analysis_report']['status']=='completed'
    assert '没有符合' in final['answer']
    restart()
    history=(await client.get('/sessions/empty-contract')).json()['messages']
    saved=history[-1]['metadata']
    assert saved['analysis_process']['coverage']==final['analysis_process']['coverage']
    assert saved['analysis_report']==final['analysis_report']


async def test_pseudo_tool_reply_is_not_saved_as_a_successful_business_answer(local_app,monkeypatch):
    client,_,_=local_app
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    await upload(client,'protocol-replay')
    decision=router.RouteDecision(route='chat',reason='常规说明',analysis_brief={
        'goal':'解释数据','requirements':['解释数据'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    calls=[]
    def invoke(messages):
        calls.append(1)
        return SimpleNamespace(content='<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="query_sql">SELECT private_column FROM sales')
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=invoke))
    response=await client.post('/ask',json={'session_id':'protocol-replay','question':'解释数据'})
    assert response.status_code==502 and '未执行的工具指令' in response.text
    assert len(calls)==1 and 'DSML' not in response.text and 'private_column' not in response.text
    saved=(await client.get('/sessions/protocol-replay')).json()['messages'][-1]
    assert saved['metadata']['error'] and '未执行的工具指令' in saved['content']
    assert 'DSML' not in saved['content'] and 'SELECT' not in saved['content']
