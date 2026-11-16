"""Questions are saved, scoped and resumed without treating old answers as new goals."""
from types import SimpleNamespace
import pytest
from app import main, session
from app.schemas import AskRequest
from test_chat_storage import storage_app  # noqa: F401


async def seed(client):
    await client.post('/sessions/clarify/upload',files={'files':('orders.csv',b'region,amount\nEast,10\nWest,20\n','text/csv')})
    return session.get_session_store()._backend


async def test_answer_retains_original_goal_and_repeated_questions_keep_confirmations(storage_app,monkeypatch):
    from app.clarification import prepare_reply, model_question, complete_question
    client,_,_=storage_app
    backend=await seed(client)
    req=AskRequest(question='比较销售额',session_id='clarify')
    final={'route':'clarify','route_reason':'使用商品金额还是支付金额？','clarification':{'question':'使用哪种金额？','options':['商品金额','支付金额']}}
    complete_question(req,final)
    backend.save_message('clarify','assistant',final['route_reason'],final)
    mid=backend.get_messages('clarify')[-1]['id']
    reply=AskRequest(question='商品金额',session_id='clarify',clarification_reply={'message_id':mid})
    prepare_reply(reply)
    assert '比较销售额' in model_question(reply) and '商品金额' in model_question(reply)
    second={'route':'clarify','route_reason':'比较哪个月份？'}
    complete_question(reply,second)
    assert second['clarification']['goal']=='比较销售额'
    assert second['clarification']['answers'][0]['answer']=='商品金额'
    backend.save_message('clarify','user',reply.question,{'clarification_reply':{'message_id':mid}})
    backend.save_message('clarify','assistant',second['route_reason'],second)
    newer=backend.get_messages('clarify')[-1]['id']
    with pytest.raises(ValueError,match='最新|已回答'):
        prepare_reply(AskRequest(question='支付金额',session_id='clarify',clarification_reply={'message_id':mid}))
    plain=AskRequest(question='七月和八月',session_id='clarify')
    prepare_reply(plain)
    assert plain.clarification_reply.message_id==newer
    assert '商品金额' in model_question(plain) and '七月和八月' in model_question(plain)


async def test_cross_chat_reply_is_rejected_before_model(storage_app,monkeypatch):
    client,_,_=storage_app
    backend=await seed(client)
    backend.save_message('clarify','assistant','哪种金额？',{'clarification':{'question':'哪种金额？','goal':'销售额','answers':[]}})
    mid=backend.get_messages('clarify')[-1]['id']
    await client.post('/sessions/other/upload',files={'files':('x.csv',b'x\n1\n','text/csv')})
    monkeypatch.setattr(main,'get_chat_llm',lambda:(_ for _ in ()).throw(AssertionError('No model call')))
    response=await client.post('/tasks',json={'question':'商品金额','session_id':'other','clarification_reply':{'message_id':mid}})
    assert response.status_code==400,response.text


async def test_investigation_evidence_is_reused_only_at_same_data_version(storage_app):
    from app.clarification import prepare_reply
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import workbench_snapshot
    client,_,_=storage_app
    backend=await seed(client)
    version=workbench_snapshot(get_dataset_manager().get('clarify'))
    step={'step_id':'total','status':'succeeded','question':'合计','sql':'SELECT SUM(amount) FROM orders','rows':[{'total':30}],'columns':['total'],'row_count':1,'error':'','attempts':[]}
    backend.save_message('clarify','assistant','哪种分组？',{'data_version':version,'clarification':{'question':'哪种分组？','goal':'分析销售','answers':[]},'investigation':{'status':'clarify','steps':[step]}})
    req=AskRequest(question='按地区',session_id='clarify');prepare_reply(req)
    assert req._investigation_resume['steps'][0]['source_message_id']
    await client.post('/sessions/clarify/upload',files={'files':('extra.csv',b'x\n1\n','text/csv')})
    changed=AskRequest(question='按地区',session_id='clarify');prepare_reply(changed)
    assert not changed._investigation_resume


async def test_resumed_planner_sees_saved_steps_without_repeating_their_query(storage_app,monkeypatch):
    import json
    from app.agent import investigation
    client,_,_=storage_app
    await seed(client)
    previous={'step_id':'total','question':'合计','status':'succeeded','sql':'SELECT SUM(amount) FROM orders',
              'rows':[{'total':30}],'columns':['total'],'row_count':1,'error':'','attempts':[],'source_message_id':17}
    seen=[]
    def plan(messages):
        seen.append(json.loads(messages[-1].content))
        return investigation.InvestigationDecision(action='finish',reason='已保存的合计已能回答已澄清的目标')
    monkeypatch.setattr(investigation,'get_structured_llm',lambda schema:SimpleNamespace(invoke=plan))
    monkeypatch.setattr(investigation,'build_graph',lambda:SimpleNamespace(invoke=lambda state:(_ for _ in ()).throw(AssertionError('Do not rerun saved SQL'))))
    result=investigation.run_investigation('沿用金额合计','clarify',resume={'steps':[previous]})
    assert result['status']=='completed' and seen[0]['steps'][0]['source_message_id']==17


async def test_null_investigation_metadata_does_not_break_a_normal_answer(storage_app):
    from app.clarification import prepare_reply
    client,_,_=storage_app
    backend=await seed(client)
    backend.save_message('clarify','assistant','哪个月份？',{'investigation':None,'data_version':None,'context_snapshot':{},'clarification':{'question':'哪个月份？','goal':'对比','answers':[]}})
    req=AskRequest(question='八月',session_id='clarify');prepare_reply(req)
    assert req._clarification_context['goal']=='对比'


async def test_field_labels_do_not_assert_unconfirmed_freight_meanings(storage_app):
    from app.clarification import complete_question
    client,_,_=storage_app
    await seed(client)
    await client.post('/sessions/clarify/upload',files={'files':('amounts.csv',b'item_amount,payment_amount\n10,12\n','text/csv')})
    final={'route':'clarify','clarification':{'question':'payment_amount（支付金额，含运费等）还是 item_amount（商品金额）？','options':['payment_amount（支付金额，含运费等）','item_amount（商品金额）']}}
    complete_question(AskRequest(question='选金额字段',session_id='clarify'),final)
    assert '含运费' not in final['clarification']['question']
    assert final['clarification']['options'][0]=='payment_amount（支付金额）'
