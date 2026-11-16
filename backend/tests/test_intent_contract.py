from types import SimpleNamespace

from app.answer_acceptance import prepare_brief, calculation_brief, dependency_answer, assess_coverage
from app.schemas import AnalysisBrief


def parts():
    return [
        {'requirement':'商品金额','criterion':{'answer_type':'value','result_columns':['amount'],'request_quote':'商品金额'}},
        {'requirement':'最好的卖家','criterion':{'answer_type':'table','result_columns':['seller_id'],
            'request_quote':'最好的卖家','intent_gap':'最好按商品金额还是履约表现判断？'}},
    ]


def test_quoted_intent_and_gap_use_the_current_request_without_hiding_supported_parts():
    brief=AnalysisBrief(goal='经营分析',parts=parts()).model_dump()
    prepared=prepare_brief({'question':'给我商品金额和最好的卖家'},brief)
    assert prepared['intent_review']['quote_bound_ids']==['R1','R2']
    assert prepared['intent_review']['status']=='unverified'
    assert [s['id'] for s in calculation_brief(prepared)['acceptance']]==['R1']
    assert '最好按商品金额还是履约表现判断' in dependency_answer(prepared)
    assert assess_coverage(prepared,{})[1]['status']=='needs_input'


def test_a_real_but_unrelated_historical_quote_cannot_certify_the_current_intent():
    raw=AnalysisBrief(goal='分析',parts=parts()[:1]).model_dump()
    prepared=prepare_brief({'question':'改看订单数量'},raw,messages=[{'id':1,'role':'user','content':'商品金额'}])
    assert prepared['intent_review']['unverified_ids']==['R1']
    assert prepared['acceptance'][0]['request_quote']==''
    assert prepared['acceptance'][0]['unverified_request_quote']=='商品金额'
    assert calculation_brief(prepared)['acceptance']  # Unverified intent is not a blanket refusal.


def test_router_known_ambiguity_does_not_execute_or_spend_a_repair_call(monkeypatch):
    from app.agent import router
    requested=parts()[1:]
    decision=router.RouteDecision(route='sql',reason='先判断',analysis_brief={'goal':'卖家','parts':requested})
    calls=[]
    def invoke(messages):calls.append(messages);return decision
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=invoke))
    result=router.router_node({'question':'最好的卖家','schema':'sellers(seller_id)'})
    assert result['route']=='clarify' and len(calls)==1
    assert '最好按' in result['dependency_response']
    assert result['analysis_brief']['intent_review']['gaps']
    assert result['clarification']['question']==requested[0]['criterion']['intent_gap']


def test_partial_answer_keeps_the_original_goal_in_its_pending_question(monkeypatch):
    from app.agent import router
    from app import clarification
    from app.schemas import AskRequest
    decision=router.RouteDecision(route='sql',reason='金额可算，排名待确认',analysis_brief={'goal':'分析','parts':parts()})
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    state=router.router_node({'question':'给我商品金额和最好的卖家','schema':'orders(amount),sellers(seller_id)'})
    assert state['route']=='sql' and state['analysis_brief']['active_requirement_ids']==['R1']
    monkeypatch.setattr(clarification.settings,'app_mode','local')
    clarification.complete_question(AskRequest(question='给我商品金额和最好的卖家'),state)
    assert state['clarification']['goal']=='给我商品金额和最好的卖家'
    assert '最好按' in state['clarification']['question']


def test_user_correction_clears_only_the_answered_intent_gap():
    original=prepare_brief({'question':'给我商品金额和最好的卖家'},AnalysisBrief(goal='分析',parts=parts()).model_dump())
    corrected=parts();corrected[1]['criterion'].update(intent_gap='',request_quote='按商品金额排名卖家')
    prepared=prepare_brief({'question':'按商品金额排名卖家'},AnalysisBrief(goal='分析',parts=corrected).model_dump(),
        messages=[{'id':1,'role':'assistant','metadata':{'analysis_brief':original}}])
    assert not prepared['intent_review']['gaps']
    assert [s['id'] for s in calculation_brief(prepared)['acceptance']]==['R1','R2']


def test_router_passes_business_framing_and_followup_rules_in_its_existing_call(monkeypatch):
    from app.agent import router
    calls=[]
    decision=router.RouteDecision(route='sql',reason='沿用',analysis_brief={'goal':'六月金额','parts':[
        {'requirement':'六月金额','criterion':{'answer_type':'value','result_columns':['amount'],'request_quote':'改成六月'}}]})
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:(calls.append(messages) or decision)))
    result=router.router_node({'question':'改成六月，其他不变','schema':'orders(amount,date)',
        'session_context':'已确认商品金额不含运费，先前看五月。'})
    prompt=calls[0][0].content
    assert 'ANALYST INTENT' in prompt and 'request_quote' in prompt and 'intent_gap' in prompt
    assert 'entity' in prompt and 'same grain' in prompt
    assert result['analysis_brief']['intent_review']['quote_bound_ids']==['R1'] and len(calls)==1
