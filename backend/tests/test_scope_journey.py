from types import SimpleNamespace
import json
import pytest

from app import main, session
from app.agent import graph, nodes, router
from app.context_memory import project_messages
from app.tools import dataset_manager
from test_chat_storage import storage_app  # noqa: F401


async def test_four_turn_scope_change_uses_real_sql_and_survives_restart(storage_app,monkeypatch):
    client,root,_=storage_app
    sid='scope-journey'
    files={
        'orders.csv':'order_id,customer_id,order_status,order_purchase_timestamp\na,c1,delivered,2018-05-10\nb,c2,delivered,2018-06-10\nc,c3,canceled,2018-06-11\n',
        'order_items.csv':'order_id,order_item_id,product_id,seller_id,price,freight_value\na,1,p,s,40,4\nb,1,p,s,60,4\nc,1,p,s,999,9\n',
        'customers.csv':'customer_id,customer_unique_id\nc1,u1\nc2,u2\nc3,u3\n',
        'payments.csv':'order_id,payment_value\na,44\nb,64\nc,1008\n',
    }
    for name,text in files.items():
        result=await client.post(f'/sessions/{sid}/upload',files={'files':(name,text.encode(),'text/csv')})
        assert result.status_code==200,result.text
    workbench=(await client.get(f'/sessions/{sid}/workbench')).json()
    confirmed=await client.post(f'/sessions/{sid}/commerce/confirm',json={
        'snapshot':workbench['snapshot'],'confirmed':True,'profile':'olist_v1','currency':'BRL'})
    assert confirmed.status_code==200,confirmed.text
    questions=['仅看2018年5月的已送达订单，统计商品金额。','改成2018年6月，其他不变。','换成支付金额，仍看2018年6月。','利润能算吗？']
    decisions=[];queries=[]
    for index,(metric,month) in enumerate([('item_amount',5),('item_amount',6),('payment_amount',6)]):
        fact,field=('payments','payment_value') if metric=='payment_amount' else ('order_items','price')
        start=f'2018-{month:02d}-01';end=f'2018-{month+1:02d}-01'
        body={'goal':questions[index],'parts':[{'requirement':'本轮金额','criterion':{
            'answer_type':'value','metric_ids':[metric],'result_columns':['amount'],'request_quote':questions[index]}}],
            'revision_quote':'' if index==0 else questions[index],
            'scope_sources':[{'key':'period','text':f'2018年{month}月'}],
            'calculation_plan':{'source_quotes':[questions[index]],'stages':[{'name':'summary','source':'orders',
                'joins':[{'table':fact,'left_keys':['order_id'],'right_keys':['order_id']}],
                'filters':[{'field':'orders.order_status','op':'eq','value':'delivered'},
                    {'field':'orders.order_purchase_timestamp','op':'ge','value':start},
                    {'field':'orders.order_purchase_timestamp','op':'lt','value':end}],
                'select':[{'name':'amount','op':'sum','field':fact+'.'+field}]}]}}
        decisions.append(router.RouteDecision(route='sql',reason='明确范围',analysis_brief=body))
        queries.append(f"SELECT SUM(f.{field}) AS amount FROM orders o JOIN {fact} f USING(order_id) WHERE o.order_status='delivered' AND o.order_purchase_timestamp>='{start}' AND o.order_purchase_timestamp<'{end}'")
    decisions.append(router.RouteDecision(route='chat',reason='利润缺实际成本',analysis_brief={'goal':questions[3],'parts':[
        {'requirement':'利润可计算性','criterion':{'answer_type':'feasibility','metric_ids':['profit'],'request_quote':questions[3]}}]}))
    route_calls=[];sql_calls=[]
    def route(messages):route_calls.append(messages);return decisions[len(route_calls)-1]
    def sql(messages):sql_calls.append(messages);return SimpleNamespace(content=queries[len(sql_calls)-1])
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=route))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=sql))
    monkeypatch.setattr(graph,'_graph',None)
    responses=[]
    for question in questions:
        response=await client.post('/ask',json={'question':question,'session_id':sid})
        assert response.status_code==200,response.text
        responses.append(response.json())
    assert [response['rows'] for response in responses[:3]]==[[{'amount':40.0}],[{'amount':60.0}],[{'amount':64.0}]]
    assert not responses[3]['rows'] and '成本' in responses[3]['answer']
    assert len(route_calls)==4 and len(sql_calls)==0
    assert all(any(c['id']=='compiled_execution' and c['status']=='passed' for c in r['checks']) for r in responses[:3])
    history=(await client.get(f'/sessions/{sid}')).json()
    analyses=[m for m in history['messages'] if m['role']=='assistant' and m['metadata'].get('analysis_scope')]
    assert len(analyses)==4
    assert [m['metadata']['analysis_scope_state']['status'] for m in analyses]==['historical_scope','historical_scope','current','current']
    assert analyses[0]['metadata']['rows']==[{'amount':40.0}]
    assert analyses[0]['metadata']['analysis_scope_state']['replacement_message_id']==analyses[2]['id']
    assert project_messages(analyses)[0]['evidence']['analysis_scope_state']['status']=='historical_scope'
    raw=session.get_session_store()._backend.get_messages(sid)
    assert not raw[-4]['metadata'].get('analysis_scope_state')  # Derived labels never rewrite stored originals.
    dataset_manager.get_dataset_manager().get(sid).close()
    monkeypatch.setattr(session,'_store',None);monkeypatch.setattr(dataset_manager,'_manager',None)
    restored=(await client.get(f'/sessions/{sid}')).json()
    assert restored==history
    (root/'scope-journey.json').write_text(json.dumps({'responses':responses,'history':history},ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.parametrize('cancelled',[False,True])
async def test_failed_or_cancelled_revision_is_saved_without_claiming_completion(storage_app,cancelled):
    from app import execution
    from app.analysis_scope import create_scope,finish_scope
    from app.schemas import AskRequest
    from app.tools.data_workbench import workbench_snapshot
    client,_,_=storage_app;sid='failed-scope'
    await client.post(f'/sessions/{sid}/upload',files={'files':('sales.csv',b'amount\n40\n','text/csv')})
    backend=session.get_session_store()._backend;dataset=dataset_manager.get_dataset_manager().get(sid)
    b={'acceptance':[{'id':'R1','answer_type':'value','result_columns':['amount']}],
        'scope_sources':[{'key':'period','text':'五月'}]}
    initial=create_scope(b,[],data_version=workbench_snapshot(dataset),table_versions={'sales':dataset.files[0].data_version})
    backend.save_message(sid,'assistant','五月金额40',{'rows':[{'amount':40}],'columns':['amount'],
        'analysis_scope':finish_scope(initial,{'rows':[{'amount':40}],'columns':['amount']})})
    b={**b,'revision_quote':'改成六月','scope_sources':[{'key':'period','text':'六月'}]}
    pending=create_scope(b,backend.get_messages(sid),data_version=workbench_snapshot(dataset),table_versions={'sales':dataset.files[0].data_version})
    token=execution._current.set(execution.AnalysisTask('qa',sid))
    try:
        execution.save_partial_result({'analysis_scope':pending,'analysis_brief':b,'session_id':sid})
        error=execution.AnalysisCancelled('停止') if cancelled else RuntimeError('controlled failure')
        main._record_analysis_failure(AskRequest(question='改成六月',session_id=sid),sid,error)
    finally:execution._current.reset(token)
    history=(await client.get(f'/sessions/{sid}')).json()['messages']
    old=next(m for m in history if m['role']=='assistant' and m['content']=='五月金额40')
    assert old['metadata']['rows']==[{'amount':40}]
    assert old['metadata']['analysis_scope_state']['recalculation_status']==('cancelled' if cancelled else 'failed')
