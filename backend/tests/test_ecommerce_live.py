import pandas as pd

from evals.ecommerce_reference import build_reference
from evals.ecommerce_scoring import score_case


def test_scoring_accepts_completed_investigation_route_and_signed_change():
    from evals.ecommerce_scoring import match_rows
    assert match_rows([{'customer_state':'SP','item_amount_diff_june_minus_may':-40,'abs_diff':40,'order_diff':-2}],
                      [{'region':'SP','delta':-40}])
    case={'target':'order_count','chart':None,'mode':'investigate'}
    record={'status':'ran','request_ids':[1],'model_calls':1,'final':{
        'route':'investigate','sql':'SELECT ...','rows':[{'order_count':5}], 'answer':'依据与范围',
        'investigation':{'status':'completed','steps':[]}}}
    assert score_case(case,record,{'order_count':[{'order_count':5}]})['checks']['execution']
    case={'target':'state_change','chart':'bar','mode':'investigate'}
    top=[{'region':'SP','delta':-40}]
    totals=[{'month':'2018-05','item_amount':100},{'month':'2018-06','item_amount':60}]
    record['final'].update(chart_spec={'data':[{'type':'bar','orientation':'h','x':[-40],'y':['SP']}]})
    record['final']['investigation']['steps']=[{'status':'succeeded','sql':'SELECT ...','rows':top},
                                              {'status':'succeeded','sql':'SELECT ...','rows':totals}]
    assert score_case(case,record,{'state_change':{'top':top,'totals':totals}})['passed']


def test_reference_keeps_item_and_payment_grains_and_customer_identity_separate():
    tables = {
        'orders': pd.DataFrame({'order_id':['a','b','c'], 'customer_id':['c1','c2','c3'],
            'order_status':['delivered','delivered','canceled'], 'order_purchase_timestamp':['2018-05-01','2018-06-01','2018-08-30']}),
        'customers': pd.DataFrame({'customer_id':['c1','c2','c3'],'customer_unique_id':['same','same','other'],'customer_state':['SP','SP','RJ']}),
        'order_items': pd.DataFrame({'order_id':['a','a','a','b','c'],'product_id':['p']*5,'price':['20','30','50','60','999'],'freight_value':['1','2','3','4','99']}),
        'payments': pd.DataFrame({'order_id':['a','a','b','c'],'payment_value':['40','66','64','1098']}),
        'products': pd.DataFrame({'product_id':['p'],'product_category_name':['livros']}),
        'category_translation': pd.DataFrame({'product_category_name':['livros'],'product_category_name_english':['books']}),
    }
    reference = build_reference(tables)
    assert reference['order_count'][0]['order_count'] == 2
    assert reference['monthly_money'][0]['item_amount'] == '100'
    assert reference['monthly_money'][0]['payment_amount'] == '106'
    assert reference['repeat_customers'] == [{'customer_count':1,'repeat_customers':1,'repeat_rate':'1'}]
    assert reference['state_change']['top'][0]['delta'] == '-40'
    assert reference['retention'][0]['retention_rate'] == '1'
    assert any(row['month_offset'] == 3 and row['retention_rate'] is None for row in reference['retention'])
    tables['orders'].loc[:,'order_purchase_timestamp'] = ['2018-06-01','2018-06-20','2018-08-30']
    tables['orders'] = pd.concat([tables['orders'],pd.DataFrame({'order_id':['d'],'customer_id':['c4'],
        'order_status':['delivered'],'order_purchase_timestamp':['2018-08-15']})],ignore_index=True)
    tables['customers'] = pd.concat([tables['customers'],pd.DataFrame({'customer_id':['c4'],'customer_unique_id':['new'],
        'customer_state':['RJ']})],ignore_index=True)
    censored = build_reference(tables)['repeat_30d']
    assert censored[0] == {'cohort':'2018-06','eligible_customers':1,'repeat_customers':1,'repeat_rate':'1'}
    assert censored[-1]['eligible_customers'] == 0 and censored[-1]['repeat_rate'] is None


def test_scoring_requires_real_requests_correct_numbers_and_requested_chart():
    case = {'target':'order_count','chart':None,'mode':'quick'}
    final = {'route':'sql','rows':[{'订单数':5008}], 'answer':'主要发现：订单数5008。数字依据见SQL。适用范围为当前样本。', 'sql':'SELECT COUNT(*) FROM orders'}
    record = {'status':'ran','request_ids':[1,2],'model_calls':2,'final':final}
    assert score_case(case,record,{'order_count':[{'order_count':5008}]})['passed']
    record['request_ids'] = []
    assert not score_case(case,record,{'order_count':[{'order_count':5008}]})['passed']
    record['request_ids'] = [1]
    record['final']['rows'][0]['订单数'] = 15024
    assert not score_case(case,record,{'order_count':[{'order_count':5008}]})['passed']


def test_investigation_with_correct_rows_but_no_chart_is_not_accepted():
    case = {'target':'state_change','chart':'bar','mode':'investigate'}
    top = [{'region':'SP','delta':'-40'}]
    totals = [{'month':'2018-05','item_amount':'100'},{'month':'2018-06','item_amount':'60'}]
    record = {'status':'ran','request_ids':[1], 'model_calls':1,'final':{'answer':'主要发现，数字依据，范围',
        'investigation':{'status':'completed','steps':[{'status':'succeeded','sql':'SELECT a','rows':totals},
          {'status':'succeeded','sql':'SELECT b','rows':top}]}}}
    result = score_case(case,record,{'state_change':{'top':top,'totals':totals}})
    assert result['checks']['numbers'] is True and result['checks']['chart'] is False
    assert result['passed'] is False


def test_reasoning_client_has_bounded_time_to_finish_a_long_sql_response():
    from app.llm import build_compatible_client
    client = build_compatible_client('https://api.deepseek.com','offline-test','deepseek-flash')
    try:
        assert client.request_timeout == 90
        assert client.max_retries == 0
        assert client.max_tokens == 16384
    finally:
        client.http_client.close()


def test_numeric_scoring_accepts_calendar_aliases_but_not_wrong_amounts():
    from evals.ecommerce_scoring import match_rows
    expected = [{'region':'SP','may_amount':'100','june_amount':'60','delta':'-40'}]
    actual = [{'customer_state':'SP','item_amount_2018_05':100,'item_amount_2018_06':60,'item_amount_diff':-40}]
    assert match_rows(actual,expected)
    actual[0]['item_amount_2018_06'] = 600
    assert not match_rows(actual,expected)


def test_display_precision_and_descriptive_suffixes_do_not_hide_wrong_counts():
    from evals.ecommerce_scoring import match_rows
    actual = [{'customer_state':'ES','valid_date_orders':53,'late_orders':17,'late_rate':.3208}]
    expected = [{'region':'ES','eligible_orders':53,'late_orders':17,'late_rate':str(17/53)}]
    assert match_rows(actual,expected)
    actual[0]['valid_date_orders'] = 54
    assert not match_rows(actual,expected)
    assert match_rows([{'first_purchase_month':'2018-08','valid_customers':0,'repeat_customers_30d':0,'repeat_rate_30d':None}],
                      [{'cohort':'2018-08','eligible_customers':0,'repeat_customers':0,'repeat_rate':None}])


def test_waterfall_scoring_uses_result_dimension_and_accepts_verified_zero_states():
    case = {'target':'waterfall','chart':'waterfall','mode':'quick'}
    record = {'status':'ran','request_ids':[1], 'model_calls':1,'final':{
        'route':'viz','sql':'SELECT ...','answer':'数字依据与范围',
        'rows':[{'state':'SP'},{'state':'SP'},{'state':'TO'},{'state':None}],
        'chart_spec':{'data':[{'type':'waterfall','x':['起点','州贡献：SP','州贡献：TO','终点'],
                              'y':[100,-40,0,60],'measure':['absolute','relative','relative','total']}]}}}
    reference = {'waterfall':{'before':'100','after':'60','contributions':[{'region':'SP','delta':'-40'}],
                              'zero_regions':['TO']}}
    assert score_case(case,record,reference)['passed']
    record['final']['chart_spec']['data'][0]['y'][2] = 1
    assert not score_case(case,record,reference)['passed']


def test_chat_does_not_receive_unfiltered_column_missing_counts(monkeypatch):
    from types import SimpleNamespace
    from app.agent import nodes
    captured=[]
    monkeypatch.setattr(nodes,'_get_llm',lambda:object())
    monkeypatch.setattr(nodes,'invoke_model',lambda llm,messages,**kwargs:captured.extend(messages) or SimpleNamespace(content='口径方案'))
    nodes.chat_node({'question':'先定方案','schema':'Table: orders\n  - delivered_date: TIMESTAMP (missing: 160)',
                     'show_data_preview':False})
    assert '160' not in captured[-1].content
    assert 'delivered_date: TIMESTAMP' in captured[-1].content


def test_order_sample_requires_full_count_while_only_first_50_rows_are_visible():
    rows=[{'order_id':str(i),'purchase_time':'2018-01-01 00:00:00','status':'delivered'} for i in range(50)]
    record={'status':'ran','request_ids':[1], 'model_calls':1,'final':{'route':'sql','sql':'SELECT ...',
        'answer':'数字依据与范围','row_count':123,'rows':rows}}
    case={'target':'order_sample','chart':None,'mode':'quick'}
    assert score_case(case,record,{'order_sample':rows})['passed']
    record['final']['row_count']=50
    assert not score_case(case,record,{'order_sample':rows})['passed']


def test_funnel_stage_and_quarter_aliases_do_not_change_numeric_or_null_checks():
    from evals.ecommerce_scoring import match_rows
    assert match_rows([{'stage':'purchase','orders':100,'conversion_rate':1}],
                      [{'stage':'下单','stage_count':100,'conversion_rate':'1'}])
    expected=[{'cohort':'2017-10','quarter_offset':3,'retention_rate':None}]
    assert match_rows([{'cohort_quarter':'2017-10-01T00:00:00','period_index':3,'retention_rate':None}],expected)
    assert not match_rows([{'cohort_quarter':'2017-10-01T00:00:00','period_index':3,'retention_rate':0}],expected)


def test_overview_scoring_checks_real_snapshot_instead_of_only_chat_success():
    import copy
    reference={'data_overview':{'orders':{'rows':2,'columns':['amount'],'null_counts':{'amount':1},'preview':[{'amount':'10'},{'amount':None}]}}}
    case={'target':'data_overview','allowed_routes':['chat']}
    record={'status':'ran','request_ids':[1],'model_calls':1,'final':{'route':'chat','answer':'数据概览',
        'table_previews':[{'table_name':'orders','rows':2,'columns':['amount'],'null_counts':{'amount':1},'preview':[{'amount':10},{'amount':None}]}]}}
    assert score_case(case,record,reference)['passed']
    wrong=copy.deepcopy(record);wrong['final']['table_previews'][0]['null_counts']['amount']=0
    assert not score_case(case,wrong,reference)['passed']
def test_retention_period_alias_preserves_exact_offsets_nulls_and_zero():
    from evals.ecommerce_scoring import match_rows
    expected=[{'cohort':'2018-01','month_offset':0,'retention_rate':'1'},
              {'cohort':'2018-01','month_offset':1,'retention_rate':'0'},
              {'cohort':'2018-01','month_offset':2,'retention_rate':None}]
    actual=[{'cohort_month':'2018-01-01','retention_month':row['month_offset'],'retention_rate':row['retention_rate']} for row in expected]
    assert match_rows(actual,expected)
    assert not match_rows([{**row,'retention_month':'2018-01'} for row in actual],expected)
    assert not match_rows([{**row,'retention_rate':0} for row in actual],expected)
