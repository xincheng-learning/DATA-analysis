from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from app import analysis_report,analysis_pipeline,execution
from app.insights import result_facts


@pytest.mark.parametrize('statuses',[['passed','failed'],['failed','passed'],['passed','not_checked']])
def test_conflicting_contract_checks_never_certify_the_report(statuses):
    state={'route':'sql','question':'统计金额','sql':'SELECT 12 AS amount',
        'columns':['amount'],'rows':[{'amount':12}],'row_count':1,
        'checks':[{'id':'calculation_contract','status':status,'detail':status} for status in statuses],
        'analysis_brief':{'requirements':['金额'],'acceptance':[
            {'requirement_index':0,'answer_type':'value','result_columns':['amount']}]}}
    delivered=analysis_pipeline.run_delivery(state)
    assert '完整结果已与声明计算计划独立对照' not in delivered['analysis_report']['verification_note']


def test_declared_scope_is_delivered_with_the_computed_table_without_a_writer(monkeypatch):
    question='输出统计表。这是合成观测，不代表真实业务总体。'
    state={'route':'python','question':question,'python_code':'result = computed','data_version':'v1',
        'columns':['n','mean'],'rows':[{'n':61,'mean':86.2}],'row_count':1,'python_output':'actual',
        'analysis_brief':{'requirements':['输出统计表','说明来源边界'],
            'acceptance':[{'requirement_index':0,'answer_type':'table','result_columns':['n','mean']},
                {'requirement_index':1,'answer_type':'scope','subject':'来源边界','scope_key':'population'}],
            'scope_sources':[{'key':'population','text':'这是合成观测，不代表真实业务总体。'}]}}
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *args:pytest.fail('a user declaration needs no synthesis call'))
    delivered=analysis_pipeline.run_delivery(state)
    assert all(item['status']=='answered' for item in delivered['analysis_process']['coverage'])
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(delivered)
    assert '按用户声明：这是合成观测，不代表真实业务总体。' in report['answer']
    assert delivered['rows']==state['rows']


def test_report_failure_keeps_table_and_program_bound_causal_limit(monkeypatch):
    from app.schemas import RequirementSpec
    spec=RequirementSpec(requirement_index=1,answer_type='explanation',subject='因果限制',method_topic='causal_boundary').model_dump()
    state={'route':'python','question':'给出统计表并解释因果限制','python_code':'result = computed','data_version':'v1',
        'columns':['n','mean'],'rows':[{'n':61,'mean':86.2}],'row_count':1,'python_output':'actual',
        'analysis_brief':{'requirements':['统计表','因果限制'],'acceptance':[
            {'requirement_index':0,'answer_type':'table','result_columns':['n','mean']},spec]}}
    def offline(messages): raise RuntimeError('controlled synthesis failure')
    monkeypatch.setattr(analysis_report,'get_structured_llm',lambda schema:SimpleNamespace(invoke=offline))
    delivered=analysis_pipeline.run_delivery(state)
    assert not delivered.get('error') and delivered['rows']==state['rows']
    assert all(item['status']=='answered' for item in delivered['analysis_process']['coverage'])
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(delivered)
    assert '统计关联不能单独证明因果' in report['answer'] and '86.2' in report['answer']


def test_all_verified_user_scope_sources_are_shown_even_without_a_separate_scope_requirement(monkeypatch):
    state=result()
    state['question']='说明金额。这是合成观测，不代表真实业务总体。'
    state['analysis_brief']['scope_sources']=[{'key':'population','text':'这是合成观测，不代表真实业务总体。'}]
    writer(monkeypatch)
    delivered=analysis_pipeline.run_delivery(state)
    from app.report_presentation import build_analysis_report
    assert '按用户声明：这是合成观测，不代表真实业务总体。' in build_analysis_report(delivered)['answer']


def result():
    rows=[{'month':'2024-02','item_amount_brl':120,'delivered_orders':3},
          {'month':'2024-03','item_amount_brl':150,'delivered_orders':5}]
    return {'route':'sql','question':'比较金额和订单，并解释利润','rows':rows,'columns':list(rows[0]),'row_count':2,
        'data_version':'v1','sql':'SELECT ...','facts':result_facts(pd.DataFrame(rows),'SELECT ...','v1'),
        'analysis_brief':{'goal':'比较金额和订单，并解释利润','requirements':['金额变化','订单变化','利润'],
            'acceptance':[{'requirement_index':0,'answer_type':'comparison','result_columns':['item_amount_brl']},
                {'requirement_index':1,'answer_type':'comparison','result_columns':['delivered_orders']},
                {'requirement_index':2,'answer_type':'value','metric_ids':['profit']}],
            'confirmed_scope':['已送达','BRL，不含运费']}}


def writer(monkeypatch,coverage=None):
    calls=[]
    def invoke(messages):
        calls.append(messages)
        return analysis_report.ReportPlan(findings=[{'text':'商品金额与订单同向增加。','reason':'两项指标来自相同范围的结果。',
            'evidence_ids':['S1.F4','S1.F7']}],coverage=coverage or [
                {'requirement_index':0,'status':'answered','evidence_ids':['S1.F4']},
                {'requirement_index':1,'status':'answered','evidence_ids':['S1.F7']},
                {'requirement_index':2,'status':'needs_input','reason':'当前计算没有提供成本和费用证据。'}])
    monkeypatch.setattr(analysis_report,'get_structured_llm',lambda schema:SimpleNamespace(invoke=invoke))
    return calls


def test_explicit_statistics_contract_delivers_values_and_binds_method_without_certifying_constraints(monkeypatch):
    from pathlib import Path
    from app.agent.router import RouteDecision
    from app.answer_acceptance import prepare_brief
    route=RouteDecision.model_validate_json((Path(__file__).with_name('fixtures')/'statistics-route-1694.json').read_text('utf-8-sig'))
    b=prepare_brief({'question':'统计与说明'},route.analysis_brief.model_dump())
    rows=[{'n':61,'mean':86.2,'std':49.1,'t_stat':13.7,'p_value':0.00001}]
    state={'route':'python','question':'统计与说明','rows':rows,'columns':list(rows[0]),'row_count':1,
        'python_code':'stats.ttest_1samp(amount,popmean=0)','data_version':'v1','analysis_brief':b}
    plan=analysis_report.ReportPlan(findings=[{'text':'检验只比较样本均值与指定基准。',
        'reason':'原假设为总体均值等于指定基准，备择假设为不等于该基准。依赖独立观测和分布适用条件，观察数据不能推断因果。',
        'requirement_ids':['R4'],'evidence_ids':['S1.E1']}],coverage=[
            {'requirement_index':i,'status':'answered','evidence_ids':['S1.R1','S1.E1']} for i in range(5)])
    monkeypatch.setattr(analysis_report,'get_structured_llm',lambda schema:SimpleNamespace(invoke=lambda messages:plan))
    delivered=analysis_pipeline.run_delivery(state)
    statuses=[item['status'] for item in delivered['analysis_process']['coverage']]
    assert statuses[:2]==['answered']*2 and statuses[3]=='answered'
    assert statuses[2]!='answered' and statuses[4]!='answered'
    assert delivered['analysis_report']['findings'][0]['requirement_ids']==['R4']


def test_common_delivery_tracks_coverage_and_reuses_its_result(monkeypatch):
    calls=writer(monkeypatch);state=result();before=deepcopy(state)
    task=execution.AnalysisTask('qa','qa');token=execution._current.set(task)
    try:delivered=analysis_pipeline.run_delivery(state)
    finally:execution._current.reset(token)
    assert state==before
    assert [entry['node'] for entry in task.trace]==['review_evidence','write_report']
    assert len(calls)==1
    coverage=delivered['analysis_process']['coverage']
    assert [item['status'] for item in coverage]==['answered','answered','needs_input']
    assert analysis_pipeline.run_delivery(delivered)==delivered and len(calls)==1
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(delivered)
    assert report['status']=='partial' and '需要补充数据或口径' in report['answer']
    assert '150.00 BRL' in report['answer']


def test_coverage_publishes_its_own_evidence_without_requiring_a_repeated_finding(monkeypatch):
    writer(monkeypatch,[{'requirement_index':0,'status':'answered','evidence_ids':['S99.R1']},
        {'requirement_index':1,'status':'answered','evidence_ids':['S1.R2']},
        {'requirement_index':2,'status':'answered','evidence_ids':[]}])
    delivered=analysis_pipeline.run_delivery(result())
    coverage=delivered['analysis_process']['coverage']
    assert [item['status'] for item in coverage]==['unanswered','linked','needs_input']
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(delivered)
    assert '[S1.R2]' in report['answer'] and '2024-03' in report['answer']


def test_rejected_prose_keeps_verified_coverage_and_execution_provenance(monkeypatch):
    def invoke(messages):
        assert 'SELECT ...' in messages[1].content and 'execution_receipt' in messages[1].content
        return analysis_report.ReportPlan(findings=[{'text':'金额增加999元。','evidence_ids':['S1.F4']}],
            coverage=[{'requirement_index':0,'status':'answered','evidence_ids':['S1.E1','S1.R2']}])
    monkeypatch.setattr(analysis_report,'get_structured_llm',lambda schema:SimpleNamespace(invoke=invoke))
    delivered=analysis_pipeline.run_delivery(result())
    assert delivered['analysis_process']['coverage'][0]['status']=='linked'
    assert '999' not in delivered['answer'] and '[S1.R2]' in delivered['answer']
    receipt=next(card for card in delivered['analysis_report']['evidence'] if card['id']=='S1.E1')
    assert receipt['sql']=='SELECT ...' and receipt['row_count']==2 and receipt['retained_rows']==2


def test_long_rejected_finding_and_twelve_references_do_not_discard_the_report(monkeypatch):
    def invoke(messages):
        return analysis_report.ReportPlan.model_validate({'findings':[
            {'text':'商品金额增加999元。'*25,'evidence_ids':['S1.F4']},
            {'text':'实际结果可逐行核对。','evidence_ids':[f'S1.R{i}' for i in range(1,13)]}],
            'coverage':[{'requirement_index':0,'status':'answered','evidence_ids':[f'S1.R{i}' for i in range(1,13)]}]})
    monkeypatch.setattr(analysis_report,'get_structured_llm',lambda schema:SimpleNamespace(invoke=invoke))
    state=result();state['rows']=[{'region':f'R{i}','amount':i} for i in range(12)];state['columns']=['region','amount'];state['row_count']=12
    state['facts']=result_facts(pd.DataFrame(state['rows']),'SELECT ...','v1')
    state['analysis_brief']['acceptance'][0]={'requirement_index':0,'answer_type':'table','result_columns':['region','amount']}
    delivered=analysis_pipeline.run_delivery(state)
    assert delivered['analysis_process']['coverage'][0]['status']=='answered'
    assert '999' not in delivered['answer'] and '[S1.R12]' in delivered['answer']


def test_conflicting_versions_do_not_invoke_writer(monkeypatch):
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *a:pytest.fail('mixed versions cannot get a synthesis'))
    a=result();a['status']='succeeded';b={**a,'data_version':'v2'}
    delivered=analysis_pipeline.run_delivery({'route':'investigate','question':'比较','investigation':{'status':'completed','steps':[a,b]}})
    assert delivered['analysis_process']['evidence_status']=='different_versions'
    assert delivered['analysis_process']['report_status']=='facts_only'


def test_failed_chart_cannot_be_marked_as_delivered_by_the_writer(monkeypatch):
    writer(monkeypatch,[{'requirement_index':0,'status':'answered','evidence_ids':['S1.F4']}])
    state=result();state['analysis_brief']['requirements']=['制作对比图表'];state['chart_error']='受控绘图失败'
    state['analysis_brief']['acceptance']=[{'requirement_index':0,'answer_type':'chart','result_columns':['item_amount_brl']}]
    delivered=analysis_pipeline.run_delivery(state)
    assert delivered['analysis_process']['coverage'][0]['status']=='linked'
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(delivered)
    assert report['status']=='partial' and '图表未完成' in report['answer'] and '150.00 BRL' in report['answer']


def test_reuse_signature_changes_when_chart_or_scope_changes():
    from app.report_presentation import report_input
    state=result();actual,_=report_input(state)
    before=analysis_report.evidence_signature(state['question'],actual,state['analysis_brief'])
    state['chart_spec']={'data':[{'type':'bar','x':['A'],'y':[1]}]};after,_=report_input(state)
    assert before!=analysis_report.evidence_signature(state['question'],after,state['analysis_brief'])


def test_no_data_and_scalar_paths_do_not_add_a_model_call(monkeypatch):
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *a:pytest.fail('unnecessary writer'))
    assert analysis_pipeline.run_delivery({'route':'chat','question':'你好','answer':'你好'})['answer']=='你好'
    scalar=analysis_pipeline.run_delivery({'route':'sql','question':'数量','rows':[{'n':7}],'columns':['n'],'row_count':1,'answer':'7'})
    assert scalar['analysis_process']['report_status']=='single_value'


@pytest.mark.parametrize('source',[{}, {'sql_source':{'message_id':12,'kind':'user-approved-sql'}}])
def test_scalar_and_saved_sql_still_receive_the_same_coverage(monkeypatch,source):
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *args:pytest.fail('no scalar writer'))
    state={'route':'sql','question':'数量','sql':'SELECT 7 AS n','rows':[{'n':7}],'columns':['n'],'row_count':1,'answer':'7',
        'analysis_brief':{'goal':'数量','requirements':['数量'],'acceptance':[{'requirement_index':0,'answer_type':'value','result_columns':['n']}]},**source}
    final=analysis_pipeline.run_delivery(state)
    assert final['analysis_process']['coverage'][0]['status']=='answered'
    assert final['answer'].startswith('7\n\n')
    assert final['analysis_report']['verification_note'] in final['answer']


def test_a_table_only_request_preserves_the_zero_writer_path(monkeypatch):
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *args:pytest.fail('table only must not add a writer'))
    state=result();state['analysis_brief']['requirements']=['完整结果表']
    state['analysis_brief']['acceptance']=[{'requirement_index':0,'answer_type':'table','result_columns':state['columns']}]
    delivered=analysis_pipeline.run_delivery(state)
    assert delivered['analysis_process']['coverage'][0]['status']=='answered'
    assert delivered['analysis_process']['report_status']=='facts'


def test_catalog_and_table_delivery_do_not_require_a_report_model(monkeypatch):
    from app.answer_acceptance import dependency_answer
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *args:pytest.fail('catalog + table needs no writer'))
    state=result();state['analysis_brief']['requirements']=['利润定义','完整结果表']
    state['analysis_brief']['acceptance']=[{'requirement_index':0,'answer_type':'definition','metric_ids':['profit']},
        {'requirement_index':1,'answer_type':'table','result_columns':state['columns']}]
    state.update(analysis_pipeline.prepare_request(state))
    delivered=analysis_pipeline.run_delivery(state)
    assert [c['status'] for c in delivered['analysis_process']['coverage']]==['answered','answered']
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(delivered)
    assert dependency_answer(state['analysis_brief']) in report['answer']


def test_successful_empty_table_keeps_columns_source_and_completion_without_writer(monkeypatch):
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *args:pytest.fail('empty table needs no writer'))
    state={'route':'sql','question':'列出amount>10的记录','sql':'SELECT amount FROM sales WHERE amount>10',
        'rows':[],'columns':['amount'],'row_count':0,'data_version':'v1',
        'analysis_brief':{'goal':'完整表','requirements':['完整表'],'acceptance':[
            {'requirement_index':0,'answer_type':'table','result_columns':['amount']}]}}
    delivered=analysis_pipeline.run_delivery(state)
    assert delivered['analysis_process']['coverage'][0]['status']=='answered'
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(delivered)
    assert report and report['sources'][0]['table']['columns']==['amount']
    assert report['sources'][0]['table']['rows']==[] and '0' in report['answer']
    assert '没有符合' in report['answer']
    assert analysis_pipeline.run_delivery({'route':'workflow','question':'记录已保存','rows':[],'columns':[]})['analysis_process']['evidence_status']=='no_analytical_result'


@pytest.mark.parametrize('kind,route',[('table','sql'),('chart','viz')])
def test_profit_table_and_chart_do_not_bypass_dependency_guard(monkeypatch,kind,route):
    from app.agent import graph,router,nodes
    from app.schemas import AnalysisBrief
    decision=router.RouteDecision(route=route,reason='测试',analysis_brief=AnalysisBrief(goal='利润',requirements=['利润交付'],
        acceptance=[{'requirement_index':0,'answer_type':kind,'metric_ids':['profit']}]))
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    monkeypatch.setattr(graph,'prepare_analysis_node',lambda state:{})
    monkeypatch.setattr(graph,'write_sql_node',lambda state:pytest.fail('missing costs must not query profit'))
    monkeypatch.setattr(nodes,'_get_llm',lambda:pytest.fail('canonical response needs no chat model'))
    final=graph.build_graph().invoke({'question':'利润交付','schema':'sales.amount'})
    assert final['route']=='chat' and '实际商品成本' in final['answer']


def test_catalog_definition_does_not_swallow_a_table_overview(tmp_path,monkeypatch):
    from app.agent import graph,router,nodes
    from app.schemas import AnalysisBrief
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import store_dataframe
    manager=DatasetManager();ds=manager.get_or_create('mixed',tmp_path)
    manager.add_file('mixed',store_dataframe('sales.csv',pd.DataFrame({'amount':[1]}),tmp_path))
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager',lambda:manager)
    decision=router.RouteDecision(route='chat',reason='介绍',show_data_preview=True,
        analysis_brief=AnalysisBrief(goal='介绍',requirements=['利润定义','可用表'],acceptance=[
            {'requirement_index':0,'answer_type':'definition','metric_ids':['profit']},
            {'requirement_index':1,'answer_type':'explanation'}]))
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    calls=[]
    def invoke(messages):
        calls.append(messages)
        return nodes.OverviewExplanation(impacts={})
    monkeypatch.setattr(nodes,'get_structured_llm',lambda schema:SimpleNamespace(invoke=invoke))
    final=graph.build_graph().invoke({'session_id':'mixed','question':'利润的定义是什么，以及介绍可用数据表','schema':'sales.amount'})
    assert len(calls)==1
    assert '实际成本' in final['answer'] and 'sales' in final['answer']
    assert final['data_dictionary'][0]['table_name']=='sales'
    assert '实际成本' in final['metric_context']['requested_answer']
    assert final['analysis_process']['coverage'][0]['status']=='answered'
    ds.close()


def test_unavailable_profit_dependencies_stop_a_quantitative_route_before_execution(monkeypatch):
    from app.agent import graph,router,nodes
    from app.schemas import AnalysisBrief
    decision=router.RouteDecision(route='python',reason='测试',analysis_brief=AnalysisBrief(goal='计算利润',requirements=['利润值'],
        acceptance=[{'requirement_index':0,'answer_type':'value','metric_ids':['profit']}]))
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:decision))
    monkeypatch.setattr(graph,'write_python_node',lambda state:pytest.fail('No cost: do not execute model Python'))
    monkeypatch.setattr(nodes,'_get_llm',lambda:pytest.fail('canonical dependency response needs no free-form chat call'))
    final=graph.build_graph().invoke({'question':'请计算利润','schema':'sales.amount'})
    assert final['route']=='chat' and '实际商品成本' in final['answer']
    assert final['analysis_process']['coverage'][0]['status']=='needs_input'


def test_delivery_preserves_external_tool_fields_and_sql_verification_stays_free(monkeypatch):
    monkeypatch.setattr(analysis_pipeline,'compose_report',lambda *a:pytest.fail('explicit verification must not call a model'))
    source={'message_id':12,'kind':'user-approved-sql'}
    verified=analysis_pipeline.run_delivery({**result(),'sql_source':source,'recommended_sql':'SELECT 1','context_snapshot':{'images':[]}})
    assert verified['sql_source']==source and verified['recommended_sql']=='SELECT 1' and verified['context_snapshot']=={'images':[]}
    assert verified['analysis_process']['report_status']=='facts_only'


def test_main_graph_runs_preparation_and_delivery_but_children_stop_at_evidence(monkeypatch):
    from app.agent import graph
    calls=writer(monkeypatch)
    monkeypatch.setattr(graph,'router_node',lambda state:{'route':'sql'})
    monkeypatch.setattr(graph,'prepare_analysis_node',lambda state:{'analysis_context':{'data_version':'v1','tables':[]}})
    monkeypatch.setattr(graph,'write_sql_node',lambda state:{'sql':'SELECT ...'})
    monkeypatch.setattr(graph,'execute_sql_node',lambda state:result())
    monkeypatch.setattr(graph,'validator_node',lambda state:{})
    task=execution.AnalysisTask('qa','qa');token=execution._current.set(task)
    try:
        user=graph.build_graph().invoke({'question':'比较金额和订单，并解释利润'})
        user_trace=[entry['node'] for entry in task.trace];task.trace=[]
        child=graph.build_graph().invoke({'question':'内部查询','internal_analysis':True})
        child_trace=[entry['node'] for entry in task.trace]
    finally:execution._current.reset(token)
    assert user_trace.index('prepare_analysis')<user_trace.index('execute_sql')<user_trace.index('review_evidence')<user_trace.index('write_report')
    assert 'analysis_process' in user and 'analysis_process' not in child
    assert 'write_report' not in child_trace and len(calls)==1


def test_time_checks_keep_missing_invalid_and_overlapping_flags_distinct():
    from app.tools.data_workbench import temporal_checks
    frame=pd.DataFrame({'order_purchase_timestamp':['2024-01-02 00:00:00']*4,
        'order_approved_at':['2024-01-03 00:00:00']*4,
        'order_delivered_carrier_date':['2024-01-01 00:00:00','2024-02-30',None,'2024-01-04T01:00:00+03:00']})
    checks=temporal_checks(frame)
    pair=next(c for c in checks if c['later_column']=='order_delivered_carrier_date' and c['earlier_column']=='order_purchase_timestamp')
    assert pair['eligible_rows']==1 and pair['reversed_rows']==1 and pair['unavailable_rows']==3
    assert pair['examples'][0]['active_row']==1
    assert len([c for c in checks if c['reversed_rows']])==2


def test_preparation_cache_expires_when_cleaning_changes_data_version(tmp_path,monkeypatch):
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import store_dataframe
    from app.tools.data_workbench import CleaningRequest,preview_cleaning,apply_cleaning,workbench_snapshot
    manager=DatasetManager();ds=manager.get_or_create('prep',tmp_path)
    manager.add_file('prep',store_dataframe('sales.csv',pd.DataFrame({'region':[' A ','A'],'amount':[2,3]}),tmp_path))
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager',lambda:manager)
    before=analysis_pipeline.prepare_analysis_node({'session_id':'prep'})['analysis_context']
    assert analysis_pipeline.prepare_analysis_node({'session_id':'prep'})['analysis_context'] is before
    plan=preview_cleaning(ds,CleaningRequest(table='sales',snapshot=workbench_snapshot(ds),rules=[{'operation':'trim','column':'region'}]))
    apply_cleaning(ds,plan['preview_id'],manager)
    after=analysis_pipeline.prepare_analysis_node({'session_id':'prep'})['analysis_context']
    assert before['data_version']!=after['data_version'] and before is not after
    ds.close()


@pytest.mark.parametrize('route',['sql','viz','python'])
def test_missing_profit_keeps_other_computation_and_sources_bound(monkeypatch,route):
    from app.agent import router,nodes
    raw=router.RouteDecision(route=route,reason='mixed',analysis_brief={
        'goal':'利润与amount','requirements':['计算利润','汇总amount'], 'acceptance':[
            {'requirement_index':0,'answer_type':'value','metric_ids':['profit']},
            {'requirement_index':1,'answer_type':'value','result_columns':['amount']}]})
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:raw))
    state={'question':'计算利润并汇总amount','schema':'sales.amount'}
    state.update(router.router_node(state))
    assert state['route']==route and state['analysis_brief']['active_requirement_ids']==['R2']
    prompts=[]
    def invoke(messages):
        prompts.append(messages[1].content)
        return SimpleNamespace(content='SELECT SUM(amount) AS amount FROM sales' if route!='python' else "result=dfs['sales'][['amount']].sum().to_frame().T")
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=invoke))
    written=(nodes.write_python_node if route=='python' else nodes.write_sql_node)(state)
    if route=='python':
        assert 'Question: 汇总amount' in prompts[0]
    else:
        assert not prompts and written['sql_retryable'] is False and not written['sql']
        assert '固定计划' in written['error']
    final=analysis_pipeline.run_delivery({**state,'rows':[{'amount':7}],'columns':['amount'],'row_count':1,'answer':'7'})
    assert [c['status'] for c in final['analysis_process']['coverage']]==['needs_input','answered']


@pytest.mark.parametrize('route,retry',[('sql',False),('sql',True),('python',False)])
def test_same_table_requirement_keeps_available_metric_and_names_missing_profit(monkeypatch,route,retry):
    import json
    from app.agent import router,nodes
    question="仅看2018-05且region='华东-限定样本'的商品金额与利润，放在同一张表"
    raw=router.RouteDecision(route=route,reason='同表两指标',analysis_brief={
        'goal':'经营指标表','requirements':[question], 'acceptance':[
            {'requirement_index':0,'answer_type':'table','metric_ids':['item_amount','profit'],
                'result_columns':['month','item_amount_brl','profit_brl']}]})
    monkeypatch.setattr(router,'_get_router_llm',lambda:SimpleNamespace(invoke=lambda messages:raw))
    state={'question':question,'schema':'sales.month,sales.amount_brl,sales.region'}
    state.update(router.router_node(state))
    if retry:state['previous_attempts']=[{'sql':'SELECT invalid FROM sales','error':'未知列'}]
    assert state['route']==route and '实际商品成本' in state['dependency_response']
    assert state['analysis_brief']['acceptance'][0]['metric_ids']==['item_amount','profit']
    def invoke(messages):
        prompt=messages[1].content
        brief=json.JSONDecoder().raw_decode(prompt.split('本轮可执行要求与已确认口径：')[1])[0]
        assert brief['acceptance'][0]['metric_ids']==['item_amount']
        assert brief['acceptance'][0]['result_columns']==['month','item_amount_brl']
        assert question in prompt and '2018-05' in prompt and "region='华东-限定样本'" in prompt
        assert brief['requirements'][0].startswith('本项只计算：')
        assert state['analysis_brief']['requirements']==[question]
        return SimpleNamespace(content='SELECT month, SUM(amount_brl) AS item_amount_brl FROM sales GROUP BY month')
    monkeypatch.setattr(nodes,'_get_llm',lambda:SimpleNamespace(invoke=invoke))
    (nodes.write_python_node if route=='python' else nodes.write_sql_node)(state)
    final=analysis_pipeline.run_delivery({**state,'rows':[{'month':'2018-05','item_amount_brl':10}],
        'columns':['month','item_amount_brl'],'row_count':1,'answer':'已保留金额结果。'})
    assert final['analysis_process']['coverage'][0]['status']=='needs_input'
    from app.report_presentation import build_analysis_report
    report=build_analysis_report(final)
    assert '实际商品成本' in report['answer'] and report['sources'][0]['table']['rows'][0]['item_amount_brl']==10
