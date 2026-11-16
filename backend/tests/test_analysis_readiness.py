"""Task-scoped preparation and execution gating, with no paid model calls."""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from app.answer_acceptance import prepare_brief
from app.tools.dataset_manager import DatasetManager
from app.tools.ingest import store_dataframe
from test_chat_storage import storage_app  # noqa: F401


@pytest.fixture
def data(tmp_path,monkeypatch):
    manager=DatasetManager();dataset=manager.get_or_create('ready',tmp_path)
    manager.add_file('ready',store_dataframe('orders.csv',pd.DataFrame({
        'amount':[10,None,900],'status':['completed','completed','cancelled'],'unused':[None,None,None]}),tmp_path))
    manager.add_file('ready',store_dataframe('unrelated.csv',pd.DataFrame({'junk':[None]}),tmp_path))
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager',lambda:manager)
    yield manager,dataset,tmp_path
    dataset.close()


def state():
    question='仅统计completed订单的商品金额，未知金额不补零'
    brief={'goal':question,'requirements':['商品金额'],'acceptance':[
        {'requirement_index':0,'answer_type':'value','result_columns':['amount'],'request_quote':question}],
        'calculation_plan':{'source_quotes':[question],'stages':[
            {'name':'total','source':'orders','select':[{'name':'amount','op':'sum','field':'orders.amount'}],
             'filters':[{'field':'orders.status','op':'eq','value':'completed'}]}]}}
    return {'session_id':'ready','route':'sql','question':question,'analysis_brief':prepare_brief({'question':question},brief)}


def test_preparation_checks_selected_inputs_and_marks_unknown_amount_limited(data,monkeypatch):
    from app.analysis_pipeline import prepare_analysis_node
    monkeypatch.setattr('app.tools.data_workbench.diagnose_data',lambda *a:pytest.fail('No repeated all-table profiling'))
    original=state();before=deepcopy(original)
    result=prepare_analysis_node(original)
    context=result['analysis_context'];readiness=context['readiness']
    assert [table['table'] for table in context['tables']]==['orders']
    assert readiness['requirements'][0]['status']=='limited'
    assert readiness['input_coverage']=={'input_rows':2,'missing_rows':{'orders.amount':1}}
    assert result['analysis_brief']['active_requirement_ids']==['R1']
    assert '不能当完整总额' in result['dependency_response']
    assert readiness['source_completeness']=='unverified' and original==before


def test_missing_profit_does_not_block_supported_sales_and_unresolved_decision_never_runs(data):
    from app.analysis_pipeline import prepare_analysis_node
    initial=state();brief=initial['analysis_brief']
    brief['requirements'].append('利润')
    brief['acceptance'].append({'id':'R2','requirement_index':1,'requirement':'利润','answer_type':'value','metric_ids':['profit']})
    brief['metric_dependencies']=[{'metric_id':'profit','status':'needs_input','reason':'缺少实际商品成本'}]
    result=prepare_analysis_node(initial)
    assert [r['status'] for r in result['analysis_context']['readiness']['requirements']]==['limited','needs_input']
    assert result['analysis_brief']['active_requirement_ids']==['R1']
    assert '实际商品成本' in result['dependency_response']
    initial=state();initial['analysis_brief']['acceptance'][0]['intent_gap']='商品金额是否包含运费？'
    result=prepare_analysis_node(initial)
    assert result['route']=='clarify' and '运费' in result['clarification']['question']
    assert result['analysis_context']['readiness']['input_coverage'] is None


def test_preparation_reuses_exact_scope_and_invalidates_changed_scope_or_version(data,monkeypatch):
    from app.analysis_pipeline import prepare_analysis_node
    manager,dataset,root=data
    calls=[];execute=dataset.execute
    monkeypatch.setattr(dataset,'execute',lambda *a,**kw:(calls.append(a[0]) or execute(*a,**kw)))
    initial=state();first=prepare_analysis_node(initial)['analysis_context']
    assert prepare_analysis_node(initial)['analysis_context'] is first and len(calls)==1
    changed=deepcopy(initial)
    changed['analysis_brief']['calculation_plan']['stages'][0]['filters'][0]['value']='cancelled'
    second=prepare_analysis_node(changed)['analysis_context']
    assert second['readiness']['input_coverage']['input_rows']==1 and len(calls)==2
    candidate=store_dataframe('orders.csv',pd.DataFrame({'amount':[5,6],'status':['completed','completed'],'unused':['','']}),root)
    manager.replace_file('ready',candidate,lambda:None)
    third=prepare_analysis_node(initial)['analysis_context']
    assert third['data_version']!=first['data_version'] and third['readiness']['requirements'][0]['status']=='ready'


def test_internal_target_gap_is_not_a_business_question_and_source_change_blocks_execution(data,monkeypatch):
    from app.analysis_pipeline import prepare_analysis_node
    from app.agent import nodes
    initial=state();initial['analysis_brief']['acceptance'][0].update(answer_type='chart',result_columns=[],target_status='unverified')
    result=prepare_analysis_node(initial)
    assert result['route']=='refuse' and '内部' in result['error'] and not result.get('clarification')
    initial=state();initial.update(prepare_analysis_node(initial))
    manager,dataset,root=data
    manager.replace_file('ready',store_dataframe('orders.csv',pd.DataFrame({'amount':[99],'status':['completed'],'unused':['']}),root),lambda:None)
    monkeypatch.setattr(nodes,'_get_llm',lambda:pytest.fail('Do not generate code using stale preparation'))
    result=nodes.write_sql_node(initial)
    assert result['sql_retryable'] is False and '数据版本已变化' in result['error']


def test_graph_stops_before_model_execution_when_preparation_requires_decision(data,monkeypatch):
    from app.agent import graph
    initial=state();initial['analysis_brief']['acceptance'][0]['intent_gap']='是否扣退款？'
    monkeypatch.setattr(graph,'router_node',lambda s:{'route':'sql'})
    monkeypatch.setattr(graph,'write_sql_node',lambda s:pytest.fail('Cannot execute before the analyst decides'))
    result=dict(graph.build_graph().invoke({**initial,'internal_analysis':True}))
    assert result['route']=='clarify' and '退款' in result['answer']


def test_missing_declared_field_blocks_without_guessing_or_executing(data,monkeypatch):
    from app.analysis_pipeline import prepare_analysis_node
    initial=state();initial['analysis_brief']['calculation_plan']['stages'][0]['select'][0]['field']='orders.missing_cost'
    monkeypatch.setattr(data[1],'execute',lambda *a,**kw:pytest.fail('Cannot probe a nonexistent field'))
    result=prepare_analysis_node(initial)
    assert result['route']=='refuse' and '未知字段' in result['error']
    assert result['analysis_brief']['active_requirement_ids']==[]


def test_nonnumeric_amount_cannot_pass_preparation_as_valid_sum(data):
    from app.analysis_pipeline import prepare_analysis_node
    manager,dataset,root=data
    manager.replace_file('ready',store_dataframe('orders.csv',pd.DataFrame({'amount':['USD 10'],'status':['completed']}),root),lambda:None)
    result=prepare_analysis_node(state())
    assert result['route']=='refuse'
    assert result['analysis_context']['readiness']['requirements'][0]['status']=='blocked'


def test_unsupported_join_coverage_and_empty_scopes_do_not_claim_ready(data):
    from app.analysis_pipeline import prepare_analysis_node
    initial=state();plan=initial['analysis_brief']['calculation_plan']
    plan['stages'].append({'name':'again','source':'total','select':[{'name':'amount','op':'column','field':'total.amount'}]})
    result=prepare_analysis_node(initial)
    assert result['analysis_context']['readiness']['requirements'][0]['status']=='limited'
    assert result['analysis_context']['readiness']['input_coverage'] is None
    initial=state();initial['analysis_brief']['calculation_plan']['stages'][0]['filters'][0]['value']='no_matches'
    result=prepare_analysis_node(initial)
    assert result['analysis_context']['readiness']['input_coverage']['input_rows']==0
    assert result['analysis_context']['readiness']['requirements'][0]['status']=='limited'


async def test_http_delivery_keeps_preparation_limit_and_persists_scoped_counts(storage_app,monkeypatch):
    from app import main
    from app.agent import router,nodes,graph
    client,_,_=storage_app
    await client.post('/sessions/ready/upload',files={'files':('orders.csv',b'amount,status\n10,completed\n,completed\n900,cancelled\n','text/csv')})
    initial=state()
    decision=router.RouteDecision(route='sql',reason='已确认口径',analysis_brief=initial['analysis_brief'])
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=lambda messages:SimpleNamespace(content="SELECT SUM(amount) amount FROM orders WHERE status='completed'")))
    monkeypatch.setattr(graph,'_graph',None)
    response=await client.post('/ask',json={'session_id':'ready','question':initial['question']})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['rows']==[{'amount':10}] and '不能当完整总额' in result['answer']
    readiness=result['analysis_process']['preparation']['readiness']
    assert readiness['requirements'][0]['status']=='limited'
    assert readiness['input_coverage']['missing_rows']=={'orders.amount':1}
    saved=(await client.get('/sessions/ready')).json()['messages'][-1]['metadata']
    assert saved['analysis_process']['preparation']['readiness']==readiness


@pytest.mark.parametrize('mode',['sql','python'])
def test_version_change_after_code_generation_stops_before_execution(data,monkeypatch,mode):
    from app.analysis_pipeline import prepare_analysis_node
    from app.agent import nodes
    initial=state();initial.update(prepare_analysis_node(initial))
    manager,dataset,root=data
    manager.replace_file('ready',store_dataframe('orders.csv',pd.DataFrame({'amount':[99],'status':['completed']}),root),lambda:None)
    initial['sql']="SELECT SUM(amount) amount FROM orders";initial['python_code']='result=99'
    monkeypatch.setattr(dataset,'execute',lambda *a,**kw:pytest.fail('No stale query execution'))
    result=(nodes.execute_sql_node if mode=='sql' else nodes.execute_python_node)(initial)
    assert '数据版本已变化' in result['error'] and not result.get('rows')
    if mode=='sql':assert result['sql_retryable'] is False
    else:assert result['python_retryable'] is False
