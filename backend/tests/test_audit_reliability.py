"""Real temporary storage and DuckDB regression cases from the November audit."""
from types import SimpleNamespace

import pytest

from app import main, session
from app.execution import TaskRegistry
from app.agent.nodes import execute_sql_node, make_chart_node, compose_answer_node
from app.tools.dataset_manager import get_dataset_manager
from test_chat_storage import storage_app  # noqa: F401
from test_task_api import wait


async def test_failed_assistant_insert_rolls_back_round_and_retains_task_answer(storage_app, monkeypatch):
    from app import context_memory
    client, _, _ = storage_app
    uploaded = await client.post('/sessions/save-failure/upload', files={'files': ('x.csv', b'x\n1\n', 'text/csv')})
    assert uploaded.status_code == 200
    backend = session.get_session_store()._backend
    before = backend.get_messages('save-failure')
    with backend._connect() as con:
        con.execute("CREATE TRIGGER reject_assistant BEFORE INSERT ON messages WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT, 'private disk failure'); END")
    monkeypatch.setattr(context_memory, 'history_context', lambda sid: ('', {}))
    monkeypatch.setattr(main, 'get_chat_llm', lambda: object())
    monkeypatch.setattr(main, 'run_agent', lambda question, session_id, **kwargs: ({'answer': '已计算的测试答案', 'route': 'chat'}, session_id))
    registry = TaskRegistry()
    monkeypatch.setattr(main, 'tasks', registry)
    try:
        response = await client.post('/tasks', json={'question': '本轮问题', 'session_id': 'save-failure'})
        assert response.status_code == 200, response.text
        task = await wait(client, response.json())
        assert task['status'] == 'failed'
        assert task['result']['answer'] == '已计算的测试答案'
        assert '保存失败' in task['error'] and 'private disk failure' not in str(task)
        assert backend.get_messages('save-failure') == before
        assert task['result']['evidence']['task_id'] == task['task_id']
    finally:
        registry.close()


async def test_new_scope_recalculates_same_step_without_using_superseded_total(storage_app, monkeypatch):
    from app.agent import investigation
    from app.clarification import prepare_reply, model_question
    from app.schemas import AskRequest
    from app.tools.data_workbench import workbench_snapshot
    from app.analysis_report import build_evidence
    client, _, _ = storage_app
    await client.post('/sessions/scope-fix/upload', files={'files': ('orders.csv', b'status,amount\ndelivered,100\ncanceled,900\n', 'text/csv')})
    data = get_dataset_manager().get('scope-fix')
    step = {'step_id':'total','question':'全部订单金额合计','status':'succeeded','sql':'SELECT SUM(amount) AS total FROM orders',
            'rows':[{'total':1000}],'columns':['total'],'row_count':1,'error':'','attempts':[]}
    backend = session.get_session_store()._backend
    backend.save_message('scope-fix','assistant','统计什么状态？',{'data_version':workbench_snapshot(data),
        'clarification':{'question':'统计什么状态？','goal':'分析销售额','answers':[]},'investigation':{'status':'clarify','steps':[step]}})
    req = AskRequest(question='只统计delivered，请重算',session_id='scope-fix')
    prepare_reply(req)
    decisions = iter([investigation.InvestigationDecision(action='analyze',step_id='total',question='仅已送达金额合计',reason='使用者更正口径'),
                      investigation.InvestigationDecision(action='finish',reason='新口径已计算')])
    monkeypatch.setattr(investigation,'get_structured_llm',lambda schema:SimpleNamespace(invoke=lambda messages:next(decisions)))
    monkeypatch.setattr(investigation,'build_graph',lambda:SimpleNamespace(invoke=lambda state: {
        **state,'sql':"SELECT SUM(amount) AS total FROM orders WHERE status='delivered'",
        **execute_sql_node({**state,'sql':"SELECT SUM(amount) AS total FROM orders WHERE status='delivered'"})}))
    result = investigation.run_investigation(model_question(req),'scope-fix',resume=req._investigation_resume)
    assert result['status'] == 'completed'
    assert result['steps'][-1]['rows'] == [{'total':100}]
    assert result['steps'][0]['rows'] == [{'total':1000}] and result['steps'][0]['superseded']
    assert all('1000' not in card['text'] for card in build_evidence(result))


def test_having_coverage_does_not_warn_about_excluded_groups():
    import duckdb
    from app.checks import aggregate_coverage
    with duckdb.connect(':memory:') as con:
        con.execute("CREATE TABLE sales AS SELECT * FROM (VALUES ('East',NULL),('West',10),('West',20)) v(region,amount)")
        checks = aggregate_coverage(con,'SELECT region,SUM(amount) AS total FROM sales GROUP BY region HAVING COUNT(amount)=COUNT(*)')
    assert checks[0]['status'] == 'not_checked'
    assert 'HAVING' in checks[0]['detail'] and not any(check['status']=='warning' for check in checks)


async def test_chart_scope_uses_actual_full_source(storage_app, monkeypatch):
    import pandas as pd
    from app.tools import viz
    from app.tools.viz import ChartSpec
    client, _, _ = storage_app
    frame = pd.DataFrame({'region':[f'R{i}' for i in range(60)],'amount':range(60)})
    await client.post('/sessions/full-chart/upload',files={'files':('sales.csv',frame.to_csv(index=False).encode(),'text/csv')})
    monkeypatch.setattr(viz,'choose_chart',lambda **kwargs:ChartSpec(chart_type='bar',x_column='region',y_column='amount',row_limit=3,title='前三',x_label='地区',y_label='金额'))
    state={'session_id':'full-chart','question':'前三名','route':'viz','sql':'SELECT * FROM sales ORDER BY amount ASC'}
    state.update(execute_sql_node(state));state.update(make_chart_node(state));state.update(compose_answer_node(state))
    assert state['chart_spec']['data'][0]['x'] == ['R59','R58','R57']
    assert '图表来源为查询返回的 60 行' in state['answer'] and '绘制 3 个' in state['answer']
    assert '固定使用前 50 行' not in state['answer']


async def test_query_result_limits_reject_without_returning_a_truncated_answer(storage_app,monkeypatch):
    from app.tools import dataset_manager
    client, _, _ = storage_app
    raw = ('id\n'+'\n'.join(str(i) for i in range(100))+'\n').encode()
    await client.post('/sessions/result-limit/upload',files=[('files',('a.csv',raw,'text/csv')),('files',('b.csv',raw,'text/csv'))])
    monkeypatch.setattr(dataset_manager,'MAX_QUERY_RESULT_ROWS',200,raising=False)
    result=execute_sql_node({'session_id':'result-limit','sql':'SELECT a.id AS a_id,b.id AS b_id FROM a CROSS JOIN b'})
    assert '结果超过' in result['error'] and result['rows']==[] and not result.get('result_id')
    monkeypatch.setattr(dataset_manager,'MAX_QUERY_RESULT_ROWS',100000)
    monkeypatch.setattr(dataset_manager,'MAX_QUERY_RESULT_BYTES',2048,raising=False)
    large=execute_sql_node({'session_id':'result-limit','sql':"SELECT repeat('x',1000) AS text FROM a"})
    assert '结果超过' in large['error'] and large['rows']==[]
    count=execute_sql_node({'session_id':'result-limit','sql':'SELECT COUNT(*) AS n FROM a'})
    assert count['rows']==[{'n':100}]
    con=get_dataset_manager().get('result-limit').get_connection()
    assert con.execute("SELECT current_setting('memory_limit')").fetchone()[0] == '244.1 MiB'


@pytest.mark.parametrize('mode', ['single', 'investigate'])
async def test_result_limit_stops_rewrites_that_would_hide_rows(storage_app, monkeypatch, mode):
    from app.agent import nodes, investigation
    from app.agent.graph import build_graph
    from app.tools import dataset_manager
    client, _, _ = storage_app
    await client.post('/sessions/stop-limit/upload', files={'files': ('sales.csv', b'id\n1\n2\n3\n4\n', 'text/csv')})
    monkeypatch.setattr(dataset_manager, 'MAX_QUERY_RESULT_ROWS', 2)
    calls, plans = [], []
    def write(messages):
        calls.append(messages)
        return SimpleNamespace(content='SELECT * FROM sales' if len(calls)==1 else 'SELECT * FROM sales LIMIT 2')
    monkeypatch.setattr(nodes, '_get_llm', lambda: SimpleNamespace(invoke=write))
    if mode == 'single':
        result = build_graph().invoke({'question':'列出所有记录','session_id':'stop-limit','schema':'Table: sales(id)',
                                      'planned_sql':True,'previous_attempts':[]})
        assert result['output_limit_reached'] and result['rows']==[]
    else:
        def plan(messages):
            plans.append(messages)
            return investigation.InvestigationDecision(action='analyze',step_id=f'list-{len(plans)}',
                question=f'列出全部记录以检查数据，第{len(plans)}步',reason='核对原始记录')
        monkeypatch.setattr(investigation, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=plan))
        result = investigation.run_investigation('检查全部记录','stop-limit')
        assert result['status']=='inconclusive' and len(result['steps'])==1
        assert len(plans)==1
        assert '结果超过' in investigation.investigation_to_state(result,'检查全部记录')['answer']
    assert len(calls)==1
