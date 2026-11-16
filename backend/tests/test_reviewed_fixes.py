from copy import deepcopy
from types import SimpleNamespace

import duckdb
import pytest

from app.answer_acceptance import prepare_brief, assess_coverage
from app.analysis_report import build_evidence


def test_custom_all_time_refund_sum_does_not_require_a_period_catalog_binding():
    b={'requirements':['已结算退款总额'],'acceptance':[{'requirement_index':0,'answer_type':'value',
        'request_quote':'仅计算已结算退款总额，输出refund_amount','result_columns':['refund_amount'],'metric_ids':['refund_amount']}],
       'calculation_plan':{'stages':[{'source':'refunds','select':[{'name':'refund_amount','op':'sum','field':'refunds.amount'}],
           'filters':[{'field':'refunds.status','op':'eq','value':'settled'}]}]}}
    dataset=SimpleNamespace(files=[SimpleNamespace(table_name='refunds',columns=['amount','status'])])
    state={'question':'退款仅settled。仅计算已结算退款总额，输出refund_amount'}
    prepared=prepare_brief(state,b,dataset=dataset)
    assert prepared['metric_dependencies']==[]
    assert prepared['acceptance'][0]['metric_ids']==[]
    period=deepcopy(b);period['acceptance'][0]['request_quote']='本期间已结算退款总额'
    assert prepare_brief({'question':'本期间已结算退款总额'},period,dataset=dataset)['metric_dependencies'][0]['status']=='needs_input'
    unknown=deepcopy(b);unknown['calculation_plan']['stages'][0]['filters'][0]['value']='pending'
    assert prepare_brief(state,unknown,dataset=dataset)['metric_dependencies'][0]['status']=='needs_input'


def test_chart_receipt_includes_category_axis_without_claiming_unplotted_measures():
    brief=prepare_brief({'question':'画地区金额'}, {'requirements':['画地区金额'],
        'acceptance':[{'requirement_index':0,'answer_type':'chart','result_columns':['region','amount']}]})
    step={'status':'succeeded','route':'viz','sql':'select region,amount from result','rows':[{'region':'华东','amount':61}],
        'columns':['region','amount'],'row_count':1,'analysis_brief':brief,
        'checks':[{'id':'chart_source','status':'passed'}],
        'chart_spec':{'data':[{'type':'bar','x':['华东'],'y':[61],'meta':{'x_column':'region','y_column':'amount'}}]}}
    cards=build_evidence({'steps':[step]});chart=next(c for c in cards if c['kind']=='chart_artifact')
    assert chart['columns']==['amount'] and chart['dimension_columns']==['region']
    report={'evidence':cards,'coverage':[{'requirement_id':'R1','evidence_ids':[chart['id']]}]}
    assert assess_coverage(brief,report)[0]['status']=='answered'
    brief['acceptance'][0]['result_columns'].append('orders')
    assert assess_coverage(brief,report)[0]['status']!='answered'


def test_temporal_scope_rejects_jointly_wrong_plan_and_sql_even_when_result_is_empty():
    from app.temporal_scope import verify_temporal_scope
    brief={'acceptance':[{'request_quote':'列出西区已完成订单order_id；无匹配时返回空表'}],
        'calculation_plan':{'stages':[{'filters':[{'field':'ordered_at','op':'ge','value':'2026-01-01'}]}]}}
    with pytest.raises(ValueError,match='时间筛选'):
        verify_temporal_scope(brief,'业务定义：一月为2026-01-01至2026-02-01。列出西区已完成订单')
    brief['calculation_plan']['stages'][0]['filters']=[]
    with duckdb.connect() as db, pytest.raises(ValueError,match='时间筛选'):
        verify_temporal_scope(brief,'列出西区订单',sql="SELECT * FROM orders WHERE ordered_at>='2026-01-01'",connection=db)


def test_whole_day_rejects_last_second_cutoff_but_keeps_explicit_clock_time():
    from app.temporal_scope import verify_temporal_scope
    brief={'acceptance':[{'request_quote':'一月包含1月31日全天'}],
        'calculation_plan':{'stages':[{'filters':[{'field':'ordered_at','op':'le','cast':'timestamp','value':'2026-01-31 23:59:59'}]}]}}
    with pytest.raises(ValueError,match='排他上界'):
        verify_temporal_scope(brief,'一月包含1月31日全天')
    brief['acceptance'][0]['request_quote']='只看到2026-01-31 23:59:59'
    verify_temporal_scope(brief,'只看到2026-01-31 23:59:59')
    brief['acceptance'][0]['request_quote']='一月全部订单'
    brief['calculation_plan']['stages'][0]['filters'][0].update(op='lt',value='2026-02-01')
    with duckdb.connect() as db:
        verify_temporal_scope(brief,'一月全部订单',sql="SELECT * FROM orders WHERE ordered_at<'2026-02-01'",connection=db)
        with pytest.raises(ValueError,match='排他上界'):
            verify_temporal_scope(brief,'一月全部订单',sql="SELECT * FROM orders WHERE ordered_at<='2026-01-31 23:59:59'",connection=db)


def test_date_shaped_product_codes_do_not_become_time_filters():
    from app.temporal_scope import verify_temporal_scope
    brief={'acceptance':[{'request_quote':'查这个产品'}],
        'calculation_plan':{'stages':[{'filters':[{'field':'product_id','op':'eq','value':'2026-01'}]}]}}
    with duckdb.connect() as db:
        verify_temporal_scope(brief,'查这个产品',sql="SELECT * FROM products WHERE product_id='2026-01'",connection=db)
