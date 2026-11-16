# ruff: noqa: F811 -- pytest resolves the imported fixture by argument name.
import json
from types import SimpleNamespace

import pytest
from test_local_api import local_app  # noqa: F401

from app.tools.data_workbench import workbench_snapshot


def advice(monkeypatch, rules=None):
    from app import cleaning_assistant as cleaning
    answer = cleaning.CleaningAdvice(summary='建议清理地区空白，金额缺失保留未知。', table='sales',
        rules=rules if rules is not None else [{'operation': 'trim', 'column': 'region'}], notes=['订单明细不按订单号去重。'])
    monkeypatch.setattr(cleaning, 'get_structured_llm', lambda schema,**options: SimpleNamespace(invoke=lambda messages: answer))
    return cleaning


async def test_chat_proposal_requires_confirmation_and_survives_restart(local_app, monkeypatch):
    client, restart, root = local_app
    await client.post('/sessions/clean-chat/upload', files={'files':('sales.csv', b'order_id,region,amount\n0007, East ,100\n0008,,\n','text/csv')})
    cleaning = advice(monkeypatch)
    from app.tools.dataset_manager import get_dataset_manager
    ds = get_dataset_manager().get('clean-chat')
    original = (root/'uploads/clean-chat/sales.parquet').read_bytes()
    snapshot = workbench_snapshot(ds)
    result = cleaning.cleaning_node({'question':'帮我检查并清洗数据','session_id':'clean-chat'})
    assert result['route'] == 'cleaning' and not result.get('sql')
    assert workbench_snapshot(ds) == snapshot
    plan = result['cleaning']['plan_id']
    response = await client.get(f'/sessions/clean-chat/cleaning/plans/{plan}')
    assert response.status_code == 200 and response.json()['status'] == 'pending'
    restart()
    applied = await client.post(f'/sessions/clean-chat/cleaning/plans/{plan}/apply', json={})
    assert applied.status_code == 200 and applied.json()['status'] == 'applied', applied.text
    after = applied.json()['current_snapshot']
    repeated = await client.post(f'/sessions/clean-chat/cleaning/plans/{plan}/apply', json={})
    assert repeated.status_code == 200 and repeated.json()['current_snapshot'] == after
    history = (await client.get('/sessions/clean-chat')).json()['messages']
    assert len([m for m in history if m['metadata'].get('cleaning_event',{}).get('action') == 'apply']) == 1
    undone = await client.post(f'/sessions/clean-chat/cleaning/plans/{plan}/undo', json={})
    assert undone.status_code == 200 and undone.json()['current_snapshot'] == snapshot
    assert (await client.post(f'/sessions/clean-chat/cleaning/plans/{plan}/apply', json={})).status_code == 409
    assert (root/'uploads/clean-chat/sales.parquet').read_bytes() == original


async def test_invalid_model_format_never_applies_or_retries_an_older_plan(local_app,monkeypatch):
    client,_,_=local_app
    await client.post('/sessions/invalid-format/upload',files={'files':('sales.csv',b'region\n East \n','text/csv')})
    cleaning=advice(monkeypatch)
    data=cleaning.dataset_manager.get_dataset_manager().get('invalid-format')
    old=cleaning.cleaning_node({'question':'清理空白','session_id':'invalid-format'})['cleaning']['plan_id']
    before=workbench_snapshot(data);calls=[]
    from langchain_core.exceptions import OutputParserException
    def invalid(messages):
        calls.append(messages);raise OutputParserException('invalid JSON')
    monkeypatch.setattr(cleaning,'get_structured_llm',lambda schema,**options:SimpleNamespace(invoke=invalid))
    result=cleaning.cleaning_node({'question':'调整清洗方案','session_id':'invalid-format'})
    assert result['error'] and result['cleaning']['status']=='invalid' and len(calls)==1
    assert workbench_snapshot(data)==before and not cleaning._state(data)['current_cleaning_plan']
    assert cleaning.plan_status(data,old)['status']=='superseded'
    target=cleaning.chat_confirmation_target('执行',data,[{'role':'assistant','metadata':result}])
    assert target.get('answer') and not target.get('plan_id')
    assert cleaning.apply_chat_confirmation(data,target,'执行')['route']=='clarify'
    assert workbench_snapshot(data)==before and len(calls)==1


async def test_superseded_cross_session_and_busy_plan_cannot_apply(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/plans/upload', files={'files':('sales.csv', b'region\n East \n','text/csv')})
    cleaning = advice(monkeypatch)
    first = cleaning.cleaning_node({'question':'清理空格','session_id':'plans'})['cleaning']['plan_id']
    second = cleaning.cleaning_node({'question':'调整清理方案','session_id':'plans'})['cleaning']['plan_id']
    assert (await client.get(f'/sessions/plans/cleaning/plans/{first}')).json()['status'] == 'superseded'
    assert (await client.post(f'/sessions/plans/cleaning/plans/{first}/apply', json={})).status_code == 409
    assert (await client.post('/sessions/plans/cleaning/apply',json={'preview_id':first})).status_code == 409
    await client.post('/sessions/other-plans/upload', files={'files':('sales.csv', b'region\n West \n','text/csv')})
    assert (await client.get(f'/sessions/other-plans/cleaning/plans/{second}')).status_code == 404
    from app import main
    monkeypatch.setattr(main.tasks, 'active_for_session', lambda sid: True)
    assert (await client.post(f'/sessions/plans/cleaning/plans/{second}/apply', json={})).status_code == 409
    assert (await client.post(f'/sessions/plans/cleaning/plans/{second}/apply',json={},headers={'origin':'https://evil.example'})).status_code == 403


async def test_cleaning_graph_has_no_sql_and_memory_keeps_only_proposed_state(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/clean-graph/upload', files={'files':('sales.csv', b'region,amount\n East ,\n','text/csv')})
    advice(monkeypatch)
    from app.agent import graph, router
    monkeypatch.setattr(router, '_get_router_llm', lambda: SimpleNamespace(invoke=lambda messages:router.RouteDecision(route='cleaning',reason='先体检并提出方案', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    result, _ = graph.run_agent('帮我清洗，不要直接执行', 'clean-graph')
    assert result['cleaning']['plan_id'] and not result.get('sql') and not result.get('chart_spec')
    from app.context_memory import project_messages
    memory = project_messages([{'id':1,'role':'assistant','content':result['answer'],'metadata':result}])
    assert memory[0]['evidence']['cleaning']['status'] == 'proposed'


async def test_investigation_can_propose_cleaning_without_executing(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/clean-investigate/upload', files={'files':('sales.csv', b'region\n East \n','text/csv')})
    advice(monkeypatch)
    from app.agent import investigation
    monkeypatch.setattr(investigation,'get_structured_llm',lambda schema:SimpleNamespace(invoke=lambda messages:investigation.InvestigationDecision(action='cleaning',reason='先诊断清洗问题')))
    result = investigation.run_investigation('先给清洗方案', 'clean-investigate')
    assert result['cleaning_result']['cleaning']['status'] == 'proposed' and result['steps'] == []


async def test_model_confirmed_fill_is_only_a_proposal_and_expiry_blocks_it(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/no-auto/upload',files={'files':('sales.csv', b'region,amount\nEast,100\nWest,\n','text/csv')})
    cleaning = advice(monkeypatch,[{'operation':'fill','column':'amount','value':0,'confirmed':True}])
    from app.tools.dataset_manager import get_dataset_manager
    ds = get_dataset_manager().get('no-auto')
    snapshot = workbench_snapshot(ds)
    result = cleaning.cleaning_node({'question':'检查一下','session_id':'no-auto'})
    assert workbench_snapshot(ds) == snapshot and ds.get_connection().execute('select count(*) from sales where amount is null').fetchone()[0] == 1
    plan = result['cleaning']['plan_id']
    path = ds.parquet_dir/'.workbench/plans'/(plan+'.json')
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved['created_at'] = 0
    path.write_text(json.dumps(saved),encoding='utf-8')
    assert (await client.post(f'/sessions/no-auto/cleaning/plans/{plan}/apply',json={})).status_code == 409


async def test_applied_data_state_remains_truthful_when_chat_record_write_fails(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/record-recovery/upload',files={'files':('sales.csv', b'region\n East \n','text/csv')})
    cleaning = advice(monkeypatch)
    result = cleaning.cleaning_node({'question':'清理空白','session_id':'record-recovery'})
    plan = result['cleaning']['plan_id']
    from app import main
    backend = main.get_session_store()._backend
    save = backend.save_message
    monkeypatch.setattr(backend,'save_message',lambda *args,**kwargs: (_ for _ in ()).throw(OSError('disk busy')))
    response = await client.post(f'/sessions/record-recovery/cleaning/plans/{plan}/apply',json={})
    assert response.status_code == 200 and response.json()['status'] == 'applied'
    assert response.json()['record_error']
    monkeypatch.setattr(backend,'save_message',save)
    assert (await client.get(f'/sessions/record-recovery/cleaning/plans/{plan}')).json()['status'] == 'applied'
    history = (await client.get('/sessions/record-recovery')).json()['messages']
    assert len([m for m in history if m['metadata'].get('cleaning_event')]) == 1


def test_direct_cleaning_confirmation_accepts_normal_phrases_not_questions_or_revisions():
    from app.cleaning_assistant import is_cleaning_confirmation
    for text in ['执行','那就执行吧','好，就按这个方案做','好的，执行吧','按这个方案做','就按你说的来','可以，按刚才的方案执行','执行清洗','按你推荐的方案处理','那你直接帮我执行清洗操作吧','那你可以直接帮我清洗吗']:
        assert is_cleaning_confirmation(text), text
    for text in ['数据清洗','先别执行','如果没问题就执行','执行是什么意思','执行？','“执行”','执行，但是金额不要填零','把表里的“执行”改成完成']:
        assert not is_cleaning_confirmation(text), text


async def test_explicit_execute_can_build_apply_and_receipt_a_missing_plan(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/execute-new/upload', files={'files':('sales.csv', b'region\n East \n','text/csv')})
    cleaning = advice(monkeypatch)
    result = cleaning.cleaning_node({'question':'那你直接帮我执行清洗操作吧',
                                    'session_id':'execute-new', 'cleaning_intent':'apply'})
    assert result['cleaning_event']['action'] == 'apply'
    assert result['cleaning']['status'] == 'applied'
    assert result['cleaning']['can_undo']


async def test_graph_keeps_the_actual_cleaning_receipt_in_the_api_response(local_app,monkeypatch):
    client,_,_=local_app
    await client.post('/sessions/graph-receipt/upload',files={'files':('sales.csv',b'region\n East \n','text/csv')})
    advice(monkeypatch)
    from app.agent import graph,router
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:
        router.RouteDecision(route='cleaning',reason='直接执行清洗',cleaning_intent='apply', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]})))
    result,_=graph.run_agent('那你直接帮我执行清洗操作吧','graph-receipt')
    assert result['cleaning_event']['action']=='apply'
    assert result['cleaning_event']['plan_id']==result['cleaning']['plan_id']


async def test_chat_execute_applies_saved_plan_without_model_and_is_idempotent(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/chat-execute/upload',files={'files':('sales.csv',b'region,amount\n East ,\n','text/csv')})
    cleaning = advice(monkeypatch)
    from app import main
    from app.schemas import AskRequest
    from app.tools.dataset_manager import get_dataset_manager
    ds = get_dataset_manager().get('chat-execute')
    proposed = cleaning.cleaning_node({'question':'数据清洗','session_id':'chat-execute'})
    backend=main.get_session_store()._backend
    backend.save_message('chat-execute','assistant',proposed['answer'],{**proposed,'cleaning_event':None})
    before=workbench_snapshot(ds)
    def no_model(*args,**kwargs):
        raise AssertionError('执行已有方案不应再次调用模型或重新生成规则')
    monkeypatch.setattr(main,'run_agent',no_model)
    monkeypatch.setattr('app.context_memory.history_context',no_model)
    result=main._analyse(AskRequest(question='执行',session_id='chat-execute'),'local-user')
    assert result.cleaning_event['action']=='apply' and result.cleaning_event['approval_text']=='执行'
    assert workbench_snapshot(ds)!=before
    assert ds.get_connection().execute('select region from sales').fetchone()[0]=='East'
    after=workbench_snapshot(ds)
    repeated=main._analyse(AskRequest(question='好的，执行吧',session_id='chat-execute'),'local-user')
    assert workbench_snapshot(ds)==after and '已经' in repeated.answer
    assert (await client.get(f"/sessions/chat-execute/cleaning/plans/{proposed['cleaning']['plan_id']}")).json()['status']=='applied'


async def test_expired_or_unrelated_chat_cannot_execute_old_cleaning_plan(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/expired-chat/upload',files={'files':('sales.csv',b'region\n East \n','text/csv')})
    cleaning=advice(monkeypatch)
    from app import main
    from app.schemas import AskRequest
    from app.tools.dataset_manager import get_dataset_manager
    ds=get_dataset_manager().get('expired-chat')
    proposed=cleaning.cleaning_node({'question':'数据清洗','session_id':'expired-chat'})
    backend=main.get_session_store()._backend
    backend.save_message('expired-chat','assistant',proposed['answer'],proposed)
    before=workbench_snapshot(ds)
    path=ds.parquet_dir/'.workbench/plans'/(proposed['cleaning']['plan_id']+'.json')
    saved=json.loads(path.read_text(encoding='utf-8'))
    saved['created_at']=0
    path.write_text(json.dumps(saved),encoding='utf-8')
    result=main._analyse(AskRequest(question='执行',session_id='expired-chat'),'local-user')
    assert result.route=='clarify' and '过期' in result.answer and workbench_snapshot(ds)==before
    backend.save_message('expired-chat','assistant','还可以分析销售额。',{'route':'chat'})
    result=main._analyse(AskRequest(question='执行',session_id='expired-chat'),'local-user')
    assert result.route=='clarify' and workbench_snapshot(ds)==before


async def test_cancelled_confirmation_does_not_start_cleaning(local_app, monkeypatch):
    client, _, _ = local_app
    await client.post('/sessions/stopped-chat/upload',files={'files':('sales.csv',b'region\n East \n','text/csv')})
    cleaning=advice(monkeypatch)
    from app import execution, main
    from app.schemas import AskRequest
    from app.tools.dataset_manager import get_dataset_manager
    ds=get_dataset_manager().get('stopped-chat')
    proposed=cleaning.cleaning_node({'question':'数据清洗','session_id':'stopped-chat'})
    main.get_session_store()._backend.save_message('stopped-chat','assistant',proposed['answer'],proposed)
    before=workbench_snapshot(ds)
    task=execution.AnalysisTask(owner='local-user',session_id='stopped-chat')
    task.cancelled.set()
    token=execution._current.set(task)
    try:
        with pytest.raises(execution.AnalysisCancelled):
            main._analyse(AskRequest(question='执行',session_id='stopped-chat'),'local-user')
    finally:
        execution._current.reset(token)
    assert workbench_snapshot(ds)==before



async def test_wrong_temporal_summary_is_not_used_as_answer_plan_or_history(local_app,monkeypatch):
    client,restart,_=local_app
    data=b'order_id,region,order_purchase_timestamp,order_approved_at,order_delivered_customer_date\na, East ,2018-05-03,2018-05-02,2018-05-04\nb,West,2018-05-03,2018-05-02,2018-05-02\nc,West,2018-05-03,2018-05-04,2018-05-02\n'
    await client.post('/sessions/time-summary/upload',files={'files':('orders.csv',data,'text/csv')})
    cleaning=advice(monkeypatch)
    wrong='14/82/1都是同一批，总共82'
    model=cleaning.CleaningAdvice(summary=wrong,table='orders',rules=[{'operation':'trim','column':'region'}],notes=[wrong,'地区只清理首尾空白。'])
    monkeypatch.setattr(cleaning,'get_structured_llm',lambda schema,**kwargs:SimpleNamespace(invoke=lambda messages:model))
    result=cleaning.cleaning_node({'question':'检查时间顺序并清理地区空白','session_id':'time-summary'})
    assert wrong not in json.dumps(result,ensure_ascii=False)
    assert '共 3 行' in result['answer'] and '共同命中 1 行' in result['answer']
    assert result['cleaning']['preview']['affected_rows']==1
    assert '实际检测到 1 行首尾空白' in result['cleaning']['notes'][0]
    from app import main
    main.get_session_store()._backend.save_message('time-summary','assistant',result['answer'],result)
    plan=result['cleaning']['plan_id'];restart()
    saved=(await client.get('/sessions/time-summary')).json()['messages'][-1]
    assert wrong not in json.dumps(saved,ensure_ascii=False)
    current=(await client.get('/sessions/time-summary/cleaning/plans/'+plan)).json()
    assert '共 3 行' in current['summary'] and current['status']=='pending'
