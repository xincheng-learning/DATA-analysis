"""Deterministic contracts checked against hand arithmetic in a non-commerce domain."""
from copy import deepcopy
import pandas as pd
import pytest

from app.business_contract import compile_requests
from app.calculation_contract import CalculationPlan,compile_plan
from test_execution_basis_workflow import manager  # noqa: F401


@pytest.fixture
def inputs(manager):
    ds=manager({'jobs':[{'job_id':'a','date':'2026-03-01','state':'done','site':'A'},
        {'job_id':'b','date':'2026-03-03','state':'done','site':'B'},
        {'job_id':'c','date':'2026-02-01','state':'done','site':'A'}],
        'lines':[{'job_id':'a','quantity':2,'rate':10},{'job_id':'a','quantity':1,'rate':5},
            {'job_id':'b','quantity':1,'rate':40},{'job_id':'c','quantity':3,'rate':100}],
        'credits':[{'job_id':'a','state':'posted','amount':2},{'job_id':'a','state':'pending','amount':100},
            {'job_id':'c','state':'posted','amount':50}]})
    quote='已确认：只统计done作业，费用按quantity×rate合计；抵扣仅posted且按原作业日期归属，无抵扣记录为0，已有未知保持未知。'
    entity={'table':'jobs','keys':['job_id'],'date_field':'date','filters':[{'field':'state','op':'eq','value':'done'}]}
    definitions={'usage':{'id':'usage','label':'费用','unit':'元','op':'sum_product','entity':entity,'fact_table':'lines',
        'fact_keys':['job_id'],'left_field':'quantity','right_field':'rate','confirmation_quote':quote},
        'credit':{'id':'credit','label':'抵扣','unit':'元','op':'sum','entity':entity,'fact_table':'credits','fact_keys':['job_id'],
            'field':'amount','filters':[{'field':'state','op':'eq','value':'posted'}],'absence':'zero','confirmation_quote':quote},
        'net':{'id':'net','label':'抵扣后金额','unit':'元','op':'subtract','numerator':'usage','denominator':'credit','confirmation_quote':quote}}
    request={'id':'result','metrics':[{'metric_id':k,'column':k} for k in ['usage','credit','net']],
        'period':{'start':'2026-03-01','end':'2026-04-01'}}
    return ds,quote,definitions,request


def run(inputs,request=None,definitions=None):
    ds,q,defs,req=inputs
    result=compile_requests(ds,definitions or defs,[request or req],q,[q])
    plan=CalculationPlan.model_validate(result['plan']);sql,columns=compile_plan(ds,plan,q,[q],authorized_windows=result['windows'])
    frame=ds.execute(sql,as_frame=True)
    return frame,result,sql


def test_facts_are_scoped_and_aggregated_independently(inputs):
    frame,_,_=run(inputs)
    assert frame.to_dict('records')==[{'usage':65,'credit':2,'net':63}]


def test_grouping_reuses_definitions_and_keeps_zero_absence(inputs):
    req=deepcopy(inputs[3]);req['group_by']=[{'field':'site','column':'site'}]
    frame,_,_=run(inputs,req)
    assert frame.sort_values('site').to_dict('records')==[{'site':'A','usage':25,'credit':2,'net':23},
        {'site':'B','usage':40,'credit':0,'net':40}]


def test_period_revision_changes_population_without_rewriting_formula(inputs):
    defs=deepcopy(inputs[2]);req=deepcopy(inputs[3]);req['period']={'start':'2026-02-01','end':'2026-03-01'}
    frame,_,_=run(inputs,req)
    assert frame.to_dict('records')==[{'usage':300,'credit':50,'net':250}]
    assert inputs[2]==defs


def test_complete_daily_frame_preserves_unobserved_day(inputs):
    req=deepcopy(inputs[3]);req['period']={'start':'2026-03-01','end':'2026-03-04'}
    req['group_by']=[{'field':'$day','column':'day'}];req['complete_days']=True
    frame,_,_=run(inputs,req)
    frame=frame.sort_values('day')
    assert len(frame)==3 and pd.isna(frame.iloc[1]['usage']) and frame.iloc[1]['credit']==0
    assert frame.iloc[0]['net']==23 and frame.iloc[2]['net']==40


def test_missing_definition_and_cycles_are_preparation_errors(inputs):
    from app.business_contract import ContractError
    definitions=deepcopy(inputs[2]);definitions['net']['numerator']='net'
    with pytest.raises(ContractError,match='循环'):run(inputs,definitions=definitions)
    del definitions['usage'];definitions['net']['numerator']='usage'
    with pytest.raises(ContractError,match='缺少'):run(inputs,definitions=definitions)


def test_fixed_ratio_scale_and_zero_denominator_stay_explicit(inputs):
    defs=deepcopy(inputs[2]);defs['share']={'id':'share','label':'抵扣比例','unit':'%','op':'ratio',
        'numerator':'credit','denominator':'usage','multiplier':100,'confirmation_quote':inputs[1]}
    req=deepcopy(inputs[3]);req['metrics']=[{'metric_id':'share','column':'share'}]
    frame,_,_=run(inputs,req,defs)
    assert frame.iloc[0]['share']==pytest.approx(100*2/65)
    defs['share']['denominator']='credit';defs['share']['numerator']='usage'
    req['group_by']=[{'field':'site','column':'site'}]
    frame,_,_=run(inputs,req,defs)
    assert pd.isna(frame.set_index('site').loc['B','share'])


def test_distinct_and_repeat_counts_preserve_unknown_identities(inputs):
    defs={'people':{'id':'people','label':'作业人员','unit':'人','op':'count_distinct',
        'entity':inputs[2]['usage']['entity'],'field':'site','confirmation_quote':inputs[1]},
        'repeat':{'id':'repeat','label':'重复作业人员','unit':'人','op':'repeat_count',
        'entity':inputs[2]['usage']['entity'],'field':'site','event_field':'job_id','confirmation_quote':inputs[1]}}
    req=deepcopy(inputs[3]);req['metrics']=[{'metric_id':k,'column':k} for k in defs]
    frame,_,_=run(inputs,req,defs)
    assert frame.to_dict('records')==[{'people':2,'repeat':0}]
    ds=inputs[0]
    rows=ds.execute('SELECT * FROM jobs',as_frame=True);rows.loc[0,'site']=None
    ds.get_connection().register('jobs',rows)
    frame,_,_=run(inputs,req,defs)
    assert pd.isna(frame.iloc[0]['people']) and pd.isna(frame.iloc[0]['repeat'])
