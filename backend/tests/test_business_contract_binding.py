from copy import deepcopy
import pytest
from app.business_contract import ContractError
from app.business_contract_binding import bind_contracts
from app.analysis_contract_store import load_registry
from app.session import Session
from test_business_contract import inputs  # noqa: F401
from test_execution_basis_workflow import manager  # noqa: F401
from test_analysis_contract_store import backend  # noqa: F401


def bound(inputs,backend,*,question=None,definitions=None,request=None,messages=()):
    ds,q,defs,req=inputs
    backend.save(Session(ds.session_id,1,1))
    return bind_contracts({'session_id':ds.session_id,'question':question or q+' 2026年3月'},
        {'metric_requests':[request or req],'metric_definitions':list((defs if definitions is None else definitions).values()),
         'acceptance':[{'metric_request_id':'result','answer_type':'table'}]},ds,messages)


def test_confirmation_pins_definitions_and_binds_actual_columns(inputs,backend):
    result=bound(inputs,backend)
    assert result['acceptance'][0]['result_columns']==['usage','credit','net']
    assert set(result['business_contract']['definition_versions'])=={'usage','credit','net'}
    assert set(load_registry(inputs[0].session_id)['active'])=={'usage','credit','net'}


def test_unconfirmed_draft_never_becomes_active(inputs,backend):
    defs=deepcopy(inputs[2]);q=inputs[1].replace('已确认：','请建议：')
    for d in defs.values():d['confirmation_quote']=q
    result=bound(inputs,backend,question=q+' 2026年3月',definitions=defs)
    assert result['contract_question'] and not load_registry(inputs[0].session_id)['active']


def test_month_followup_uses_same_versions_without_definitions(inputs,backend):
    first=bound(inputs,backend);req=deepcopy(inputs[3]);req['period']={'start':'2026-02-01','end':'2026-03-01'}
    second=bound(inputs,backend,question='改成2026年2月，其他不变',definitions={},request=req)
    assert second['business_contract']['definition_versions']==first['business_contract']['definition_versions']
    assert second['business_contract_hash']!=first['business_contract_hash']


def test_month_revision_cannot_silently_redefine_formula(inputs,backend):
    first=bound(inputs,backend);defs={'usage':deepcopy(inputs[2]['usage'])};defs['usage']['right_field']='quantity'
    result=bound(inputs,backend,question='改成2026年3月，其他不变',definitions=defs,
                 messages=[{'id':1,'role':'user','content':inputs[1]}])
    assert result['contract_question']
    assert load_registry(inputs[0].session_id)['active']==first['business_contract']['definition_versions']


def test_forged_quote_and_wrong_period_do_not_write(inputs,backend):
    with pytest.raises(ContractError,match='真实用户'):bound(inputs,backend,question='2026年3月费用多少')
    with pytest.raises(ContractError,match='期间'):bound(inputs,backend,question=inputs[1]+' 2026年2月')
    assert not load_registry(inputs[0].session_id)['active']


def test_invalid_fact_field_does_not_persist_any_definition(inputs,backend):
    defs=deepcopy(inputs[2]);defs['usage']['right_field']='missing'
    with pytest.raises(ContractError,match='字段不存在'):bound(inputs,backend,definitions=defs)
    assert not load_registry(inputs[0].session_id)['active']


def test_explicit_schema_accepts_request_without_model_stage_plan(inputs):
    from app.schemas import AnalysisBrief
    result=AnalysisBrief.model_validate({'goal':'费用','parts':[{'requirement':'费用合计',
        'criterion':{'answer_type':'table','metric_request_id':'result'}}],
        'metric_requests':[inputs[3]]})
    assert result.calculation_plan is None and result.acceptance[0].metric_request_id=='result'


def test_real_pipeline_executes_contract_without_sql_model(inputs,backend,monkeypatch):
    from app.analysis_pipeline import prepare_request
    from app.agent import nodes
    from app import session
    from types import SimpleNamespace
    backend.save(Session(inputs[0].session_id,1,1))
    monkeypatch.setattr(session,'get_session_store',lambda:SimpleNamespace(_backend=backend))
    monkeypatch.setattr(nodes,'_get_llm',lambda:pytest.fail('No SQL model for confirmed contract'))
    ds,q,defs,req=inputs
    state={'session_id':ds.session_id,'question':q+' 2026年3月','schema':ds.schema_summary()}
    brief={'requirements':['费用、抵扣和净额'],'acceptance':[{'requirement_index':0,'answer_type':'table',
        'metric_request_id':'result','request_quote':'2026年3月'}],
        'metric_definitions':list(defs.values()),'metric_requests':[req]}
    state.update(prepare_request(state,brief))
    assert not state['analysis_brief'].get('contract_error')
    state.update(nodes.write_sql_node(state))
    assert not state.get('error'),state.get('error')
    result=nodes.execute_sql_node(state)
    assert result['rows']==[{'usage':65,'credit':2,'net':63}]
    assert any(c['id']=='business_definition' and c['status']=='passed' for c in result['checks'])
    changed=deepcopy(state)
    changed['analysis_brief']['calculation_plan']['stages'][2]['select'][0]['name']='tampered'
    assert nodes.write_sql_node(changed)['error']


def test_pipeline_reports_preparation_error_without_free_sql(inputs,backend,monkeypatch):
    from app.analysis_pipeline import prepare_request
    from app import session
    from types import SimpleNamespace
    monkeypatch.setattr(session,'get_session_store',lambda:SimpleNamespace(_backend=backend))
    result=prepare_request({'session_id':inputs[0].session_id,'question':'2026年3月'},
        {'requirements':['费用'],'acceptance':[{'requirement_index':0,'answer_type':'table','metric_request_id':'result'}],
         'metric_requests':[inputs[3]]})
    assert result['analysis_brief']['contract_error']
    assert result['analysis_brief']['calculation_plan'] is None


def test_changed_active_definition_invalidates_frozen_execution(inputs,backend):
    from app.business_contract_binding import contract_compile_inputs
    from app.analysis_contract_store import save_definition
    result=bound(inputs,backend)
    definition=deepcopy(inputs[2]['usage']);definition['right_field']='quantity'
    save_definition(inputs[0].session_id,definition)
    with pytest.raises(ContractError,match='版本已变化'):
        contract_compile_inputs(result,inputs[0].session_id)


def test_pending_confirmation_reuses_exact_draft_and_original_request(inputs,backend):
    from app.business_contract_binding import bind_contracts
    defs=deepcopy(inputs[2]);q=inputs[1].replace('已确认：','建议：')
    for d in defs.values():d['confirmation_quote']=q
    draft=bound(inputs,backend,question=q+' 2026年3月',definitions=defs)
    draft['requirements']=['费用、抵扣和净额']
    draft['scope_sources']=[{'key':'selected_period','text':'2026年3月'}]
    confirmed=bind_contracts({'session_id':inputs[0].session_id,'question':'确认采用以上全部指标定义'},
        {},inputs[0],[{'id':1,'role':'user','content':q+' 2026年3月'},
                     {'id':2,'role':'assistant','content':'建议','metadata':{'analysis_brief':draft}}])
    assert confirmed['business_contract']['definition_versions']
    assert confirmed['business_contract']['requests'][0]['period']==inputs[3]['period']
    assert all(d['confirmation_quote']=='确认采用以上全部指标定义' for d in load_registry(inputs[0].session_id)['versions'].values())


def test_multigrain_child_keeps_fixed_contract_without_rebinding(inputs,backend,monkeypatch):
    from app.analysis_pipeline import prepare_request
    from app.agent.workflow import calculation_workflow
    from app.agent import nodes
    from app import session
    from types import SimpleNamespace
    backend.save(Session(inputs[0].session_id,1,1))
    monkeypatch.setattr(session,'get_session_store',lambda:SimpleNamespace(_backend=backend))
    ds,q,defs,req=inputs
    grouped={**req,'id':'by_site','group_by':[{'field':'site','column':'site'}]}
    state={'session_id':ds.session_id,'question':q+' 2026年3月'}
    brief={'requirements':['总计','各站点'],'acceptance':[
        {'requirement_index':i,'answer_type':'table','metric_request_id':ref} for i,ref in enumerate(['result','by_site'])],
        'metric_definitions':list(defs.values()),'metric_requests':[req,grouped]}
    state.update(prepare_request(state,brief))
    assert not state['analysis_brief'].get('contract_error')
    flow=calculation_workflow(state['analysis_brief'])
    assert flow and len(flow.steps)==2
    actual=[]
    for step in flow.steps:
        child={**state,'internal_analysis':True,'analysis_brief':{**state['analysis_brief'],
            'calculation_plan':step.arguments['calculation_plan'],
            'active_requirement_ids':[f'R{i+1}' for i in step.covers]}}
        child.update(prepare_request(child))
        child.update(nodes.write_sql_node(child))
        assert not child.get('error'),child.get('error')
        result=nodes.execute_sql_node(child)
        assert not result.get('error'),result.get('error')
        actual.append(result['rows'])
    assert actual==[[{'usage':65,'credit':2,'net':63}],
        [{'site':'A','usage':25,'credit':2,'net':23},{'site':'B','usage':40,'credit':0,'net':40}]]


def test_bad_derived_definition_returns_validation_receipt_not_500(inputs,backend,monkeypatch):
    import json
    from types import SimpleNamespace
    from app.agent import router
    from app import session
    from langchain_core.exceptions import OutputParserException
    monkeypatch.setattr(session,'get_session_store',lambda:SimpleNamespace(_backend=backend))
    invalid={'route':'sql','reason':'计算','analysis_brief':{'goal':'时长净额',
        'parts':[{'requirement':'时长净额','criterion':{'answer_type':'value','metric_request_id':'result'}}],
        'metric_definitions':[{'id':'net','label':'净时长','unit':'小时','op':'subtract','confirmation_quote':'已确认'}],
        'metric_requests':[inputs[3]]}}
    calls=[]
    def invoke(messages):
        calls.append(messages)
        raise OutputParserException('Invalid contract',llm_output=json.dumps(invalid,ensure_ascii=False))
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=invoke))
    result=router.router_node({'session_id':inputs[0].session_id,'schema':inputs[0].schema_summary(),'question':'净时长'})
    assert result['route']=='refuse' and len(calls)==1
    assert '派生指标' in result['checks'][0]['detail']


def test_planner_schema_requires_derived_operands_and_excludes_stage_language():
    from app.agent.router import RouteDecision
    schema=RouteDecision.model_json_schema()
    assert 'calculation_plan' not in schema['$defs']['AnalysisBrief']['properties']
    assert 'CalculationPlan' not in schema['$defs']
    assert schema['$defs']['MetricDefinition']['allOf'][0]['then']['required']==['numerator','denominator']


def test_omitted_followup_period_is_resolved_from_confirmed_scope_not_all_time(inputs,backend):
    first=bound(inputs,backend)
    request=deepcopy(inputs[3]);request.pop('period')
    result=bind_contracts({'session_id':inputs[0].session_id,'question':'再按站点看'},
        {'metric_requests':[request],'scope_sources':[{'key':'selected_period','text':'2026年3月'}]},inputs[0],[])
    assert result['business_contract']['requests'][0]['period']==inputs[3]['period']
    assert result['business_contract']['definition_versions']==first['business_contract']['definition_versions']


def test_cleaning_replays_same_contract_in_isolated_inputs(inputs,backend):
    from app.cleaning_impact import _evaluate
    brief=bound(inputs,backend);ds,q,_,_=inputs
    reference={'brief':brief,'question':q+' 2026年3月','confirmed':[q]}
    before=_evaluate(ds,reference,'lines')
    changed=ds.execute('SELECT * FROM lines',as_frame=True);changed.loc[0,'rate']=20
    after=_evaluate(ds,reference,'lines',frame=changed)
    assert before['status']==after['status']=='computed'
    assert before['rows']==[{'usage':65,'credit':2,'net':63}]
    assert after['rows']==[{'usage':85,'credit':2,'net':83}]
    assert ds.execute('SELECT rate FROM lines LIMIT 1',as_frame=True).iloc[0,0]==10
