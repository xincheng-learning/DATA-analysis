"""Controlled language choices; real history, query execution and numerical reports."""
from types import SimpleNamespace

from test_local_api import local_app  # noqa: F401
from app import main
from app.agent import nodes, router


async def test_followups_keep_scope_both_metrics_and_stop_at_missing_costs(local_app, monkeypatch):
    client, restart, _ = local_app
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    decisions=iter([
        router.RouteDecision(route='chat',reason='确认口径', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]}),
        router.RouteDecision(route='sql',reason='比较已提供的商品金额与订单数', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]}),
        router.RouteDecision(route='sql',reason='继续核对用户指定期间的相同口径', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]}),
        router.RouteDecision(route='clarify',reason='当前表未提供成本与费用，无法据此计算利润。请提供成本字段或成本表。', analysis_brief={'goal':'原有路线回归','requirements':['原有路线回归'],'acceptance':[{'requirement_index':0,'answer_type':'explanation'}]}),
    ])
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:next(decisions)))
    prompts=[]
    answers=iter(['已确认按已送达订单的商品金额分析，BRL，不含运费。',
        "SELECT month, SUM(amount) AS item_amount_brl, COUNT(DISTINCT order_id) AS delivered_orders FROM sales WHERE order_status='delivered' GROUP BY month ORDER BY month",
        "SELECT month, SUM(amount) AS item_amount_brl, COUNT(DISTINCT order_id) AS delivered_orders FROM sales WHERE order_status='delivered' AND month IN ('2018-06','2018-07') GROUP BY month ORDER BY month"])
    def reply(messages):
        prompts.append('\n'.join(message.content for message in messages))
        return SimpleNamespace(content=next(answers))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=reply))
    await client.post('/sessions/turn-quality/upload',files={'files':('sales.csv',b'month,order_id,amount,order_status\n2018-06,a,100,delivered\n2018-07,b,150,delivered\n2018-07,c,50,delivered\n2018-07,d,500,canceled\n','text/csv')})
    questions=['按已送达商品金额分析，币种BRL，不含运费。请记住这个口径。',
        '比较2018年6月和7月，同时回答商品金额和去重订单数的变化。',
        '继续核对这两个月，两个指标都要保留。','那利润可以计算吗？']
    results=[]
    for question in questions:
        response=await client.post('/ask',json={'question':question,'session_id':'turn-quality'})
        assert response.status_code==200,response.text
        results.append(response.json())
    assert '不含运费' in prompts[1] and 'BRL' in prompts[2]
    for result in results[1:3]:
        assert [row['item_amount_brl'] for row in result['rows']]==[100,200]
        assert [row['delivered_orders'] for row in result['rows']]==[1,2]
        labels=[row['指标'] for row in result['analysis_report']['sources'][0]['table']['rows']]
        assert '商品金额（BRL）' in labels and '已送达订单数' in labels
    assert results[3]['route']=='clarify' and '成本' in results[3]['answer']
    assert results[3]['rows']==[]
    restart()
    history=(await client.get('/sessions/turn-quality')).json()['messages']
    assert len([message for message in history if message['role']=='assistant'])==4
    assert questions[-1] in [message['content'] for message in history]
