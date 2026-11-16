"""One explicit analyst decision per material gap; no model requests."""
from types import SimpleNamespace

import pytest

from app import clarification,session,main
from app.schemas import AskRequest
from test_chat_storage import storage_app  # noqa: F401


def brief():
    return {'goal':'分析销售与利润','requirements':['销售额','利润'],
        'acceptance':[
            {'id':'R1','requirement_index':0,'requirement':'销售额','answer_type':'value','result_columns':['sales'],
             'intent_gap':'销售额是否包含运费？'},
            {'id':'R2','requirement_index':1,'requirement':'利润','answer_type':'value','result_columns':['profit'],
             'intent_gap':'成本使用哪张表？'}]}


def proposed():
    return {'question':'请确认口径','decisions':[
        {'question':'销售额是否包含运费？','why':'运费会改变销售额的业务定义。',
         'options':['不含运费','含运费'],'recommendation':'不含运费','impact':'影响销售额和后续利润。'},
        {'question':'与本轮无关的决定','options':['删除全部记录'],'recommendation':'删除全部记录'}]}


def test_program_collects_all_gaps_keeps_reasons_and_does_not_invent_recommendations():
    grouped=clarification.group_decisions(brief(),proposed())
    assert [d['id'] for d in grouped['decisions']]==['R1','R2']
    first,second=grouped['decisions']
    assert first['recommendation']=='不含运费' and first['why'] and first['impact']
    assert second['recommendation']=='' and second['options']==[]
    assert '与本轮无关' not in str(grouped)


async def seed(client):
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    await client.post('/sessions/grouped/upload',files={'files':('orders.csv',b'amount\n10\n','text/csv')})
    version=workbench_snapshot(get_dataset_manager().get('grouped'))
    pending=clarification.group_decisions(brief(),proposed())
    pending.update(goal='分析销售与利润',answers=[])
    store=session.get_session_store()._backend
    store.save_message('grouped','assistant','请确认口径',{'clarification':pending,'data_version':version})
    return store,store.get_messages('grouped')[-1]['id']


async def test_grouped_answers_become_exact_user_decisions_and_preserve_goal(storage_app):
    client,_,_=storage_app
    _,mid=await seed(client)
    req=AskRequest(question='提交口径',session_id='grouped',clarification_reply={
        'message_id':mid,'answers':{'R1':'不含运费','R2':'使用实际商品成本表 costs'}})
    clarification.prepare_reply(req)
    assert req._clarification_context['goal']=='分析销售与利润'
    assert [a['answer'] for a in req._clarification_context['answers']]==['不含运费','使用实际商品成本表 costs']
    assert '不含运费' in req.question and '销售额是否包含运费？' in req.question
    assert '使用实际商品成本表 costs' in clarification.model_question(req)
    assert clarification.user_metadata(req)['clarification_reply']['answers']['R1']=='不含运费'


@pytest.mark.parametrize('answers',[{'R1':'不含运费'},{'R1':'不含运费','R2':'costs','unexpected':'删除' }])
async def test_incomplete_or_extra_answers_stop_before_model(storage_app,monkeypatch,answers):
    client,_,_=storage_app
    _,mid=await seed(client)
    monkeypatch.setattr(main,'get_chat_llm',lambda:pytest.fail('Do not call model for incomplete decisions'))
    response=await client.post('/tasks',json={'question':'确认','session_id':'grouped','clarification_reply':{'message_id':mid,'answers':answers}})
    assert response.status_code==400,response.text


async def test_changed_data_invalidates_grouped_suggestion_before_submission(storage_app):
    client,_,_=storage_app
    _,mid=await seed(client)
    await client.post('/sessions/grouped/upload',files={'files':('more.csv',b'x\n1\n','text/csv')})
    with pytest.raises(ValueError,match='数据.*变化'):
        clarification.prepare_reply(AskRequest(question='确认',session_id='grouped',clarification_reply={
            'message_id':mid,'answers':{'R1':'含运费','R2':'costs'}}))


def test_router_returns_all_pending_decisions_without_extra_model_call(monkeypatch):
    from app.agent import router
    raw=brief()
    raw['acceptance']=[{k:v for k,v in s.items() if k not in {'id','requirement'}} for s in raw['acceptance']]
    decision=router.RouteDecision(route='sql',reason='需要分析师确认',analysis_brief=raw,clarification=proposed())
    calls=[]
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:(calls.append(messages) or decision)))
    result=router.router_node({'question':'分析销售与利润','schema':'orders(amount)'})
    assert result['route']=='clarify' and len(calls)==1
    assert [d['id'] for d in result['clarification']['decisions']]==['R1','R2']


async def test_http_confirmation_preserves_each_decision_and_rejects_a_second_submission(storage_app,monkeypatch):
    client,_,_=storage_app
    _,mid=await seed(client)
    calls=[]
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    def run(question,**kwargs):
        calls.append(question)
        assert '分析销售与利润' in question and '不含运费' in question and 'costs' in question
        assert '建议不是用户决定' in question
        return {'route':'chat','answer':'已保留两项明确决定，继续原目标。'},'grouped'
    monkeypatch.setattr(main,'run_agent',run)
    payload={'question':'确认','session_id':'grouped','clarification_reply':{'message_id':mid,'answers':{'R1':'不含运费','R2':'costs'}}}
    response=await client.post('/ask',json=payload)
    assert response.status_code==200,response.text
    result=response.json()
    assert result['clarification_context']['goal']=='分析销售与利润'
    assert len(result['clarification_context']['answers'])==2
    saved=(await client.get('/sessions/grouped')).json()['messages'][-2]
    assert saved['metadata']['clarification_reply']['answers']==payload['clarification_reply']['answers']
    assert '不含运费' in saved['content'] and 'costs' in saved['content']
    assert (await client.post('/ask',json=payload)).status_code==400
    assert len(calls)==1


def test_a_suggestion_outside_its_options_is_not_published_as_a_preferred_choice():
    raw=proposed();raw['decisions'][0]['recommendation']='支付额就是销售额'
    assert clarification.group_decisions(brief(),raw)['decisions'][0]['recommendation']==''


def test_unbound_suggestions_cannot_create_new_business_decisions():
    assert clarification.group_decisions({'acceptance':[]},proposed())['decisions']==[]


async def test_confirmed_task_keeps_its_committed_json_result_for_event_stream(storage_app,monkeypatch):
    from app.execution import TaskRegistry
    from test_task_api import wait
    client,_,_=storage_app
    _,mid=await seed(client)
    registry=TaskRegistry()
    monkeypatch.setattr(main,'tasks',registry)
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(main,'run_agent',lambda question,**kwargs:({'route':'chat','answer':'继续原目标。'},'grouped'))
    try:
        response=await client.post('/tasks',json={'question':'确认','session_id':'grouped','clarification_reply':{
            'message_id':mid,'answers':{'R1':'不含运费','R2':'costs'}}})
        assert response.status_code==200,response.text
        task=await wait(client,response.json())
        assert task['status']=='succeeded'
        streamed=await client.get(f"/tasks/{task['task_id']}/events")
        assert streamed.status_code==200 and '继续原目标' in streamed.text
        assert 'clarification_context' in streamed.text
    finally:registry.close()


async def test_grouped_confirmation_resumes_real_sql_with_only_the_analysts_choices(storage_app,monkeypatch):
    from app.agent import router,nodes,graph
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    client,_,_=storage_app
    sid='confirmed-sql'
    csv=b'month,amount,freight,status\n2026-09,100,1000,completed\n2026-10,120,2000,completed\n2026-10,9000,9000,cancelled\n'
    assert (await client.post(f'/sessions/{sid}/upload',files={'files':('orders.csv',csv,'text/csv')})).status_code==200
    original='按月份比较销售额。'
    gaps={'acceptance':[{'id':'R1','requirement':'销售额','intent_gap':'销售额是否含运费？'},
        {'id':'R2','requirement':'订单范围','intent_gap':'是否包含取消单？'}]}
    pending=clarification.group_decisions(gaps);pending.update(goal=original,answers=[])
    store=session.get_session_store()._backend
    store.save_message(sid,'assistant','请确认口径',{'clarification':pending,'data_version':workbench_snapshot(get_dataset_manager().get(sid))})
    mid=store.get_messages(sid)[-1]['id']
    decisions={'R1':'仅商品金额，不含运费','R2':'仅统计completed订单'}
    decision=router.RouteDecision(route='sql',reason='沿用分析师明确确认',analysis_brief={
        'goal':original,'parts':[{'requirement':'按月份汇总商品金额','criterion':{'answer_type':'table','result_columns':['month','amount']}}],
        'scope_sources':[{'key':'amount_basis','text':decisions['R1']},{'key':'population','text':decisions['R2']}],
        'calculation_plan':{'source_quotes':list(decisions.values()),'stages':[{'name':'monthly','source':'orders',
            'select':[{'name':'month','op':'column','field':'orders.month'},{'name':'amount','op':'sum','field':'orders.amount'}],
            'filters':[{'field':'orders.status','op':'eq','value':'completed'}],'group_by':['orders.month'],'order_by':[{'field':'month'}]}],'ordered_rows':True}})
    calls=[]
    def route(messages):
        assert original in messages[-1].content and all(value in messages[-1].content for value in decisions.values())
        return decision
    def sql(messages):
        calls.append(messages)
        return SimpleNamespace(content="SELECT month,SUM(amount) amount FROM orders WHERE status='completed' GROUP BY month ORDER BY month")
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=route))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=sql))
    monkeypatch.setattr(graph,'_graph',None)
    response=await client.post('/ask',json={'question':'确认口径','session_id':sid,'clarification_reply':{'message_id':mid,'answers':decisions}})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['rows']==[{'month':'2026-09','amount':100},{'month':'2026-10','amount':120}],result.get('error')
    assert len(calls)==1 and result['clarification_context']['goal']==original
    assert any(check['id']=='calculation_contract' and check['status']=='passed' for check in result['checks'])
