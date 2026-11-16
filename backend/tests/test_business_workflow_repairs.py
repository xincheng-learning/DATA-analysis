"""Business invariants, independent of the commerce golden answers."""
import pandas as pd
import pytest
from app.calculation_contract import CalculationPlan,compile_plan
from test_execution_basis_workflow import manager  # noqa: F401
from test_chat_storage import storage_app  # noqa: F401


def test_scalar_delivery_is_not_replaced_by_a_later_daily_column():
    from app.agent.workflow import calculation_workflow
    b={'requirements':['总量','每日走势'],'acceptance':[
        {'id':'R1','requirement':'总量','answer_type':'value','result_columns':['amount']},
        {'id':'R2','requirement':'每日走势','answer_type':'chart','result_columns':['day','amount']}],
        'calculation_plan':{'source_quotes':['总量和每日走势'],'stages':[
            {'name':'summary','source':'events','select':[{'name':'amount','op':'sum','field':'amount'}]},
            {'name':'daily','source':'events','group_by':['day'],'select':[{'name':'day','op':'column','field':'day'},{'name':'amount','op':'sum','field':'amount'}]}]}}
    flow=calculation_workflow(b)
    assert flow is not None and len(flow.steps)==2
    assert flow.steps[0].arguments['calculation_plan']['stages'][-1]['name']=='summary'
    assert flow.steps[0].covers==[0] and flow.steps[1].covers==[1]


def test_first_event_assignment_counts_all_events_but_keeps_one_entity(manager):
    ds=manager({'events':[{'id':1,'person':'a','channel':'X','day':'2026-01-01'},
        {'id':2,'person':'a','channel':'Y','day':'2026-01-02'},
        {'id':3,'person':'b','channel':'Y','day':'2026-01-01'}]})
    question='每人按首笔事件唯一归属渠道，事件数统计所有事件。'
    plan=CalculationPlan.model_validate({'source_quotes':[question],'stages':[
        {'name':'assigned','source':'events','partition_by':['person'],'first_by':[{'field':'day'},{'field':'id'}],
         'select':[{'name':'person','op':'column','field':'person'},{'name':'channel','op':'column','field':'channel'},
            {'name':'events','op':'count_distinct_over','field':'id'}]}]})
    sql,_=compile_plan(ds,plan,question)
    assert ds.execute(sql,as_frame=True).sort_values('person').to_dict('records')==[
        {'person':'a','channel':'X','events':2},{'person':'b','channel':'Y','events':1}]


def test_calendar_keeps_absent_unknown_and_zero_distinct(manager):
    ds=manager({'readings':[{'day':'2026-03-01','value':None},{'day':'2026-03-03','value':0}]})
    question='列出2026-03-01至2026-03-03三天，缺失保留未知。'
    plan=CalculationPlan.model_validate({'source_quotes':[question],'stages':[
        {'name':'days','source':'calendar','calendar':{'column':'day','start':'2026-03-01','end':'2026-03-03'},
         'select':[{'name':'day','op':'column','field':'calendar.day'}]},
        {'name':'result','source':'days','joins':[{'table':'readings','kind':'left','left_keys':['day'],'right_keys':['day']}],
         'select':[{'name':'day','op':'column','field':'days.day'},{'name':'value','op':'column','field':'readings.value'}]}]})
    sql,_=compile_plan(ds,plan,question)
    rows=ds.execute(sql,as_frame=True).sort_values('day')
    assert len(rows)==3 and pd.isna(rows.iloc[0]['value']) and pd.isna(rows.iloc[1]['value']) and rows.iloc[2]['value']==0


def test_business_explanation_can_be_text_instead_of_numeric_placeholders(manager):
    ds=manager({'targets':[{'region':'A','value':None},{'region':'B','value':0}]})
    question='说明哪些地区未提供目标。'
    plan=CalculationPlan.model_validate({'source_quotes':[question],'stages':[
        {'name':'result','source':'targets','select':[{'name':'region','op':'column','field':'region'},
          {'name':'reason','op':'case','condition':{'field':'value','op':'is_null'},'value':'未提供目标','else_value':'已提供目标'}]}]})
    sql,_=compile_plan(ds,plan,question)
    assert ds.execute(sql,as_frame=True).sort_values('region')['reason'].tolist()==['未提供目标','已提供目标']


def test_provided_business_context_survives_even_when_model_did_not_extract_it():
    from app.answer_acceptance import prepare_brief
    first='数据说明：events是事件表。\n\n业务背景：已确认posted为有效事件，按原事件日期归属。\n\n当前问题：先按渠道汇总。'
    raw={'requirements':['渠道汇总'],'acceptance':[{'requirement_index':0,'answer_type':'table','result_columns':['channel','amount']}]}
    b=prepare_brief({'question':first},raw)
    messages=[{'id':1,'role':'user','content':first},{'id':2,'role':'assistant','content':'结果','metadata':{'analysis_brief':b}}]
    continued=prepare_brief({'question':'改看上月，其他不变'},raw,messages=messages)
    assert any('posted为有效事件' in s['text'] and s['message_id']==1 for s in continued['scope_sources'])


def test_entity_counts_cannot_count_cross_location_rows_as_unique_devices(manager):
    import duckdb
    ds=manager({'events':[{'device':'a','location':'X'},{'device':'a','location':'Y'},{'device':'b','location':'Y'}]})
    question='统计唯一设备数，不能累计跨地区重复设备。'
    plan=CalculationPlan.model_validate({'source_quotes':[question],'stages':[
        {'name':'total','source':'events','entity_key':'device','select':[{'name':'devices','op':'count_rows'}]}]})
    sql,_=compile_plan(ds,plan,question)
    with pytest.raises(duckdb.Error,match='实体粒度不唯一'):ds.execute(sql,as_frame=True)
    plan.stages[0].select[0].op='count_distinct';plan.stages[0].select[0].field='device'
    sql,_=compile_plan(ds,plan,question)
    assert ds.execute(sql,as_frame=True).iloc[0]['devices']==2


def test_memory_does_not_repeat_full_executable_plans_and_report_tables():
    from copy import deepcopy
    from app.context_memory import project_messages
    message={'id':1,'role':'assistant','content':'已计算','metadata':{'analysis_brief':{
        'requirements':['总量'],'scope_sources':[{'key':'rule','text':'保留未知','source_question':'原话'*200}],
        'calculation_plan':{'stages':['long plan']},'acceptance':[]},
        'analysis_report':{'brief':{'duplicate':'long plan'},'evidence':[{'id':'same rows'}],'coverage':[]}}}
    original=deepcopy(message)
    evidence=project_messages([message])[0]['evidence']
    assert 'calculation_plan' not in evidence['analysis_brief']
    assert 'source_question' not in evidence['analysis_brief']['scope_sources'][0]
    assert 'brief' not in evidence['analysis_report'] and 'evidence' not in evidence['analysis_report']
    assert message==original


def test_stage_reference_typo_is_resolved_only_when_output_binding_is_unique():
    from app.answer_acceptance import prepare_brief
    b={'requirements':['均值'],'acceptance':[{'requirement_index':0,'answer_type':'value','result_columns':['average'],'result_stage':'summary'}],
        'calculation_plan':{'source_quotes':['均值'],'stages':[
            {'name':'summary','source':'events','select':[{'name':'total','op':'sum','field':'amount'}]},
            {'name':'derived','source':'summary','select':[{'name':'average','op':'column','field':'total'}]}]}}
    result=prepare_brief({'question':'均值'},b)
    assert result['acceptance'][0]['result_stage']=='derived'
    assert result['acceptance'][0]['result_columns']==['average']


def test_conditional_sum_keeps_unknown_inputs_inside_the_selected_subset(manager):
    ds=manager({'readings':[{'period':'A','value':4},{'period':'B','value':None},{'period':'B','value':2}]})
    q='分别计算A和B期间，任意未知输入使该期间总量未知。'
    plan=CalculationPlan.model_validate({'source_quotes':[q],'stages':[{'name':'result','source':'readings','select':[
        {'name':k,'op':'sum_if','field':'value','condition':{'field':'period','op':'eq','value':k},'null_policy':'require_complete'} for k in ['A','B']]}]})
    sql,_=compile_plan(ds,plan,q);row=ds.execute(sql,as_frame=True).iloc[0]
    assert row['A']==4 and pd.isna(row['B'])


def test_classification_distinguishes_missing_zero_and_positive_targets(manager):
    ds=manager({'targets':[{'id':1,'value':None},{'id':2,'value':0},{'id':3,'value':10}]})
    q='区分缺失目标、零目标与正目标。'
    plan=CalculationPlan.model_validate({'source_quotes':[q],'stages':[{'name':'result','source':'targets','select':[
        {'name':'reason','op':'case','cases':[{'condition':{'field':'value','op':'is_null'},'value':'缺目标'},
            {'condition':{'field':'value','op':'eq','value':0},'value':'零目标：完成率不定义'}],'else_value':'正目标'}], 'order_by':[]} ]})
    sql,_=compile_plan(ds,plan,q)
    assert ds.execute(sql,as_frame=True)['reason'].tolist()==['缺目标','零目标：完成率不定义','正目标']


def test_explicit_daily_window_is_prepared_even_if_model_only_selected_recorded_dates(manager):
    from app.answer_acceptance import prepare_brief
    ds=manager({'readings':[{'day':'2026-03-30','value':7},{'day':'2026-04-02','value':0}]})
    q='报告窗口为2026-03-30至2026-04-02（含首尾）。请给出每日已知读数，缺失不补零。'
    raw={'requirements':['每日读数'],'acceptance':[{'requirement_index':0,'answer_type':'table','result_columns':['day','value'],'result_stage':'daily'}],
        'calculation_plan':{'source_quotes':[q],'stages':[{'name':'daily','source':'readings','group_by':['day'],
            'select':[{'name':'day','op':'column','field':'day'},{'name':'value','op':'sum','field':'value','null_policy':'known_subtotal'}]}]}}
    b=prepare_brief({'question':q},raw,dataset=ds)
    plan=CalculationPlan.model_validate(b['calculation_plan']);sql,_=compile_plan(ds,plan,q)
    frame=ds.execute(sql,as_frame=True).sort_values('day')
    assert len(frame)==4 and frame.iloc[0]['value']==7 and frame.iloc[3]['value']==0
    assert pd.isna(frame.iloc[1]['value']) and pd.isna(frame.iloc[2]['value'])
    assert ds.execute('SELECT COUNT(*) FROM readings').fetchone()[0]==2


def test_subtotals_preserve_a_confirmed_product_formula(manager):
    from app.formula_contract import verify_user_formulas
    ds=manager({'runs':[{'job':'a','power':2,'hours':3},{'job':'b','power':4,'hours':5}]})
    q='能耗为power×hours合计。'
    plan=CalculationPlan.model_validate({'source_quotes':[q],'stages':[
        {'name':'jobs','source':'runs','group_by':['job'],'select':[{'name':'energy','op':'sum_product','numerator':'power','denominator':'hours'}]},
        {'name':'total','source':'jobs','select':[{'name':'energy','op':'sum','field':'jobs.energy'}]}]})
    b={'acceptance':[{'id':'R1','requirement':'能耗','result_columns':['energy']}], 'scope_sources':[]}
    assert verify_user_formulas(ds,b,plan,q)[0]['status']=='passed'


def test_dimension_lookup_cannot_multiply_an_already_aggregated_result(manager):
    import duckdb
    ds=manager({'usage':[{'region':'A','value':3}], 'rates':[{'region':'A','period':'old','rate':5},{'region':'A','period':'new','rate':2}]})
    q='按地区汇总本期用量，关联本期费率。'
    raw={'source_quotes':[q],'stages':[
        {'name':'totals','source':'usage','group_by':['region'],'select':[{'name':'region','op':'column','field':'region'},{'name':'used','op':'sum','field':'value'}]},
        {'name':'result','source':'totals','joins':[{'table':'rates','kind':'left','left_keys':['region'],'right_keys':['region']}],
         'select':[{'name':'region','op':'column','field':'totals.region'},{'name':'rate','op':'column','field':'rates.rate'}]}]}
    sql,_=compile_plan(ds,CalculationPlan.model_validate(raw),q)
    with pytest.raises(duckdb.Error,match='关联粒度'):ds.execute(sql,as_frame=True)
    raw['stages'].insert(1,{'name':'current_rate','source':'rates','filters':[{'field':'period','op':'eq','value':'new'}],
        'select':[{'name':'region','op':'column','field':'region'},{'name':'rate','op':'column','field':'rate'}]})
    raw['stages'][-1]['joins'][0]['table']='current_rate'
    raw['stages'][-1]['select'][-1]['field']='current_rate.rate'
    sql,_=compile_plan(ds,CalculationPlan.model_validate(raw),q)
    assert ds.execute(sql,as_frame=True).to_dict('records')==[{'region':'A','rate':2}]


async def test_cleaning_compares_metrics_and_retains_requested_samples(storage_app):
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import preview_cleaning,CleaningRequest,workbench_snapshot
    from app.answer_acceptance import prepare_brief
    client,_,_=storage_app;sid='metrics-and-samples'
    await client.post(f'/sessions/{sid}/upload',files={'files':('events.csv',b'id,status,amount\na,done,10\nb, done ,20\n','text/csv')})
    ds=get_dataset_manager().get(sid);q='预览去除status空格，比较status精确等于done的金额、样本；保留原数据。'
    raw={'requirements':['金额与样本'],'acceptance':[{'requirement_index':0,'answer_type':'table','result_columns':['id','amount']}],
        'calculation_plan':{'source_quotes':[q],'stages':[
            {'name':'sample','source':'events','filters':[{'field':'status','op':'eq','value':'done'}],
             'select':[{'name':'id','op':'column','field':'id'},{'name':'amount','op':'column','field':'amount'}]},
            {'name':'total','source':'sample','select':[{'name':'total','op':'sum','field':'amount'},{'name':'count','op':'count_rows'}]}]}}
    state={'question':q,'analysis_brief':prepare_brief({'question':q},raw,dataset=ds)}
    report=preview_cleaning(ds,CleaningRequest(table='events',snapshot=workbench_snapshot(ds),rules=[{'operation':'trim','column':'status'}]),analysis_state=state)['analysis_impact']
    assert report['status']=='verified'
    assert report['before']['rows']==[{'total':10,'count':1}] and report['after']['rows']==[{'total':30,'count':2}]
    assert len(report['before']['samples'][0]['rows'])==1 and len(report['after']['samples'][0]['rows'])==2


def test_related_raw_measure_cannot_silently_drop_the_shared_period(manager):
    ds=manager({'events':[{'id':1,'date':'2026-01-01'},{'id':2,'date':'2026-02-01'}],
        'adjustments':[{'id':1,'amount':80},{'id':2,'amount':50}]})
    q='只看2026年2月事件及其关联调整金额。'
    raw={'source_quotes':[q],'stages':[
        {'name':'selected','source':'events','filters':[{'field':'date','op':'ge','value':'2026-02-01'},{'field':'date','op':'lt','value':'2026-03-01'}],
         'select':[{'name':'id','op':'column','field':'id'}]},
        {'name':'total','source':'adjustments','select':[{'name':'amount','op':'sum','field':'amount'}]}]}
    with pytest.raises(ValueError,match='共同期间'):
        compile_plan(ds,CalculationPlan.model_validate(raw),q)
    raw['stages'][-1]['joins']=[{'table':'selected','left_keys':['id'],'right_keys':['id']}]
    sql,_=compile_plan(ds,CalculationPlan.model_validate(raw),q)
    assert ds.execute(sql,as_frame=True).iloc[0]['amount']==50


def test_raw_sample_comparison_reports_descriptive_statistics_without_metric_claims():
    from app.cleaning_impact import _changes
    reference={'brief':{'calculation_plan':{'stages':[{'name':'sample','source':'events','select':[
        {'name':'id','op':'column','field':'id'},{'name':'amount','op':'column','field':'amount'}]}]}}}
    before={'columns':['id','amount'],'numeric_columns':['amount'],'rows':[{'id':'a','amount':10},{'id':'b','amount':None}]}
    after={**before,'rows':[*before['rows'],{'id':'c','amount':20}]}
    changes=_changes(reference,before,after)
    assert any(c['metric']=='样本行数' and c['before']==2 and c['after']==3 for c in changes)
    assert any(c['metric']=='amount · 已知值小计（样本统计）' and c['delta']=='20' for c in changes)


def test_left_join_filter_cannot_turn_all_entities_into_only_event_entities(manager):
    ds=manager({'entities':[{'id':1,'value':10},{'id':2,'value':20}], 'events':[{'id':2,'state':'posted','amount':2}]})
    q='统计全部实体金额，事件只统计posted，不能排除无事件实体。'
    raw={'source_quotes':[q],'stages':[{'name':'result','source':'entities',
        'joins':[{'table':'events','kind':'left','left_keys':['id'],'right_keys':['id']}],
        'filters':[{'field':'events.state','op':'eq','value':'posted'}],
        'select':[{'name':'value','op':'sum','field':'entities.value'}]}]}
    with pytest.raises(ValueError,match='LEFT JOIN右侧筛选'):
        compile_plan(ds,CalculationPlan.model_validate(raw),q)


async def test_explicit_undo_uses_saved_action_without_model_replanning(storage_app,monkeypatch):
    from app import main
    from app.agent import router,graph
    from app.tools.dataset_manager import get_dataset_manager
    from app.tools.data_workbench import preview_cleaning,CleaningRequest,workbench_snapshot,apply_cleaning
    client,_,_=storage_app;sid='direct-undo'
    await client.post(f'/sessions/{sid}/upload',files={'files':('events.csv',b'id,state\na, posted \n','text/csv')})
    ds=get_dataset_manager().get(sid)
    plan=preview_cleaning(ds,CleaningRequest(table='events',snapshot=workbench_snapshot(ds),rules=[{'operation':'trim','column':'state'}]))
    apply_cleaning(ds,plan['preview_id'],get_dataset_manager())
    from app.agent.workflow import saved_undo_plan
    for question in ['不要撤销刚才的清洗','如果撤销刚才的清洗会怎样？','撤销刚才的清洗，然后重算销售额。']:
        assert saved_undo_plan({'question':question,'session_id':sid}) is None
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(router,'_get_router_llm',lambda:pytest.fail('An explicit saved undo must not call a model'))
    monkeypatch.setattr(graph,'_graph',None)
    response=await client.post('/ask',json={'session_id':sid,'question':'撤销刚才的清洗，回到原批次版本，告诉我哪些数字和图需要重新计算。'})
    assert response.status_code==200,response.text
    assert '已撤销' in response.json()['answer'] and '历史' in response.json()['answer']
    assert ds.execute('SELECT state FROM events').fetchone()[0]==' posted '


def test_calendar_can_complete_an_existing_observation_projection(manager):
    ds=manager({'readings':[{'day':'2026-06-01','value':8},{'day':'2026-06-03','value':0}]})
    q='报告窗口为2026-06-01至2026-06-03，逐日展示读数，未知保留。'
    plan=CalculationPlan.model_validate({'source_quotes':[q],'stages':[
        {'name':'result','source':'readings','calendar':{'column':'day','start':'2026-06-01','end':'2026-06-03'},
         'select':[{'name':'day','op':'column','field':'readings.day'},{'name':'value','op':'column','field':'readings.value'}]}]})
    sql,_=compile_plan(ds,plan,q);frame=ds.execute(sql,as_frame=True).sort_values('day')
    assert len(frame)==3 and frame.iloc[0]['value']==8 and pd.isna(frame.iloc[1]['value']) and frame.iloc[2]['value']==0


def test_updating_a_report_keeps_its_previous_chart_obligation():
    from app.answer_acceptance import prepare_brief
    old_q='给出月度金额与每日图。'
    old={'requirements':['月度金额','每日图'],'acceptance':[
        {'id':'R1','requirement_index':0,'answer_type':'value','result_columns':['amount'],'requirement':'月度金额'},
        {'id':'R2','requirement_index':1,'answer_type':'chart','result_columns':['day','amount'],'requirement':'每日图'}]}
    messages=[{'id':1,'role':'user','content':old_q},{'id':2,'role':'assistant','content':'报告','metadata':{'analysis_brief':old}}]
    brief={'requirements':['金额'],'acceptance':[{'requirement_index':0,'answer_type':'value','result_columns':['amount']} ]}
    continued=prepare_brief({'question':'只看A渠道，其他条件不变，更新这份简报。'},brief,messages=messages)
    assert any(s.get('answer_type')=='chart' and s['result_columns']==['day','amount'] for s in continued['acceptance'])
    dropped=prepare_brief({'question':'更新这份简报，只要金额，不要图。'},brief,messages=messages)
    assert not any(s.get('answer_type')=='chart' for s in dropped['acceptance'])
