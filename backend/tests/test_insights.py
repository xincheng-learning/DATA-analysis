import pandas as pd
import pytest

from app.agent.nodes import compose_answer_node
from app.insights import result_facts


def test_single_row_metrics_answer_all_requested_values_without_fake_extrema():
    frame = pd.DataFrame({'item_amount':[50588.49], 'freight_amount':[9748.1],
                          'payment_amount':[60336.57], 'average_order_amount':[140.915013927577]})
    answer = compose_answer_node({'route':'sql','rows':frame.to_dict('records'),'columns':list(frame),
                                 'row_count':1,'facts':result_facts(frame,'SELECT ...','v')})['answer']
    main = answer.split('数字依据')[0]
    assert '客单商品金额' in main and '140.915014' in main
    assert '50,588.49' in main and '60,336.57' in main
    assert '最高' not in main and '最低' not in main


def test_money_change_chart_explains_net_and_offsetting_contributions():
    rows=[{'region':'甲','change':-120},{'region':'乙','change':40}]
    answer=compose_answer_node({'route':'viz','rows':rows,'columns':['region','change'],'row_count':2,
        'chart_spec':{'data':[{'type':'bar','orientation':'h','y':['甲','乙'],'x':[-120,40],'name':'金额变化额（元）'}],'layout':{}}})['answer']
    assert '图示各项金额变化合计为 -80' in answer
    assert '甲' in answer and '150.00%' in answer and '抵消' in answer
    assert '算术贡献' in answer and '因果' in answer
    share=compose_answer_node({'route':'viz','rows':rows,'columns':['region','change'],'row_count':2,
        'chart_spec':{'data':[{'type':'bar','x':['甲','乙'],'y':[-.1,.05],'name':'营收占比变化（%）'}],'layout':{}}})['answer']
    assert '图示各项金额变化合计' not in share


def test_cumulative_repeat_heatmap_describes_rates_and_small_observed_denominators():
    rows=[{'cohort':'2018-01','window_days':30,'observable_customers':100,'repeat_rate':.02},
          {'cohort':'2018-06','window_days':90,'observable_customers':2,'repeat_rate':0}]
    answer=compose_answer_node({'route':'viz','rows':rows,'columns':list(rows[0]),'row_count':2,
        'chart_spec':{'data':[{'type':'heatmap','x':[30,90],'y':['2018-01','2018-06'],'z':[[.02,None],[None,0]]}],'layout':{'title':'累计复购率'}}})['answer']
    assert '累计复购率' in answer and '2.00%' in answer
    assert '可观测样本仅为 2' in answer
    assert 'window_days最高' not in answer


def test_facts_come_from_full_sql_result_not_chart_first_50():
    frame = pd.DataFrame({'region': [f'r{i}' for i in range(120)], 'sales': range(120)})
    facts = result_facts(frame, 'SELECT region,sales FROM data', 'version-a')
    maximum = next(f for f in facts if f.get('statistic') == 'max')
    assert maximum['value'] == 119 and maximum['dimensions'] == {'region': 'r119'}
    assert maximum['data_version'] == 'version-a' and maximum['sql']
    answer = compose_answer_node({'route':'viz', 'sql':'SELECT region,sales FROM data', 'data_version':'version-a',
        'rows':frame.head(50).to_dict('records'), 'columns':list(frame.columns), 'row_count':120,
        'facts':facts, 'chart_spec':{'data':[{'type':'bar'}]}})['answer']
    assert '119' in answer and 'r119' in answer and '本次 SQL 完整返回的 120 行' in answer
    assert '固定使用前 50 行' not in answer
    assert '未经业务验证' in answer and '因果' in answer


def test_unknowns_are_recorded_and_partial_fallback_never_claims_full_result():
    frame = pd.DataFrame({'sales': [None, 0, 20]})
    facts = result_facts(frame, 'SELECT sales FROM data', 'v')
    assert any(f.get('missing') == 1 for f in facts)
    answer = compose_answer_node({'route':'viz','rows':[{'sales':20}], 'columns':['sales'],
        'row_count':120,'chart_spec':{'data':[{'type':'bar'}]}})['answer']
    assert '仅依据已保留的 1 行' in answer
    assert '全量最大' not in answer


@pytest.mark.parametrize('flag',['is_partial_month','is_observation_end_month_partial'])
def test_boolean_observation_warning_is_included_in_chart_narrative(flag):
    frame = pd.DataFrame({'month':['2018-07','2018-08'],'amount':[100,80],flag:[False,True]})
    facts = result_facts(frame,'SELECT ...','version')
    answer = compose_answer_node({'route':'viz','rows':frame.to_dict('records'),'columns':list(frame),
        'row_count':2,'facts':facts,'chart_spec':{'data':[{'type':'scatter'}]}})['answer']
    assert '不完整' in answer and '2018-08' in answer


@pytest.mark.parametrize('status',['incomplete_observation_month','incomplete month (data through 2018-08-30)',
                                  'Incomplete: observation month; data only through 2018-08-30'])
def test_text_observation_flags_warn_without_treating_complete_as_incomplete(status):
    frame = pd.DataFrame({'month':['2018-07','2018-08'], 'amount':[100,80],
                          'month_completeness':['complete',status]})
    facts = result_facts(frame,'SELECT ...','version')
    answer = compose_answer_node({'route':'viz','rows':frame.to_dict('records'),'columns':list(frame),
        'row_count':2,'facts':facts,'chart_spec':{'data':[{'type':'scatter'}]}})['answer']
    assert '数据覆盖不完整' in answer and '涉及 1 条记录' in answer
    frame['month_completeness'] = 'complete'
    assert not any(f.get('warning') and f['value'] for f in result_facts(frame,'SELECT ...','version'))


def test_false_complete_month_flag_is_an_incomplete_period_not_a_missing_value():
    facts=result_facts(pd.DataFrame({'month':['2018-07','2018-08'],'is_complete_month':[True,False]}),'SELECT ...','v')
    assert any(f.get('warning') and f['value']==1 and f['dimensions'][0]['month']=='2018-08' for f in facts)


def test_waterfall_explains_change_and_contributions_instead_of_step_order():
    state = {'route':'viz','sql':'SELECT ...','columns':['step_order','amount'],
        'rows':[{'step_order':0,'amount':100},{'step_order':1,'amount':-30},{'step_order':2,'amount':70}],
        'row_count':3,'chart_spec':{'data':[{'type':'waterfall','x':['5月','SP','6月'],
            'y':[100,-30,70],'measure':['absolute','relative','total']}]}}
    answer = compose_answer_node(state)['answer']
    assert '减少 30' in answer and '30.00%' in answer and 'SP' in answer
    assert '加总核对' in answer and '差额 0' in answer
    assert 'step_order 的最大' not in answer


def test_monthly_conclusion_answers_change_and_peak_in_business_words():
    frame=pd.DataFrame({'month':['2018-01','2018-02','2018-03'],'item_amount':[100.,180.,150.]})
    facts=result_facts(frame,'SELECT ...','v')
    answer=compose_answer_node({'route':'viz','rows':frame.to_dict('records'),'columns':list(frame),
        'facts':facts,'row_count':3,'chart_spec':{'data':[{'type':'scatter'}]}})['answer']
    main=answer.split('数字依据')[0]
    assert '商品金额' in main and '增加 50' in main and '50.00%' in main
    assert '2018-02' in main and '180' in main and 'item_amount' not in main


def test_retention_conclusion_excludes_day_zero_from_repeat_range():
    answer=compose_answer_node({'route':'viz','sql':'SELECT ...','row_count':6,'rows':[],'columns':[],
        'chart_spec':{'data':[{'type':'heatmap','x':[0,1,2],'y':['2018-01','2018-02'],
          'z':[[1,.05,.02],[1,0,None]],'colorbar':{'title':{'text':'retention_rate'}}}]}})['answer']
    main=answer.split('数字依据')[0]
    assert '留存' in main and '5.00%' in main and '100' not in main and '空白' in main


def test_retention_chart_without_numeric_offsets_keeps_a_safe_generic_description():
    state={'route':'viz','sql':'SELECT ...','rows':[{'retention_count':12}],'columns':['retention_count'],'row_count':1,
        'chart_spec':{'data':[{'type':'heatmap','x':['active'],'y':['new'],'z':[[12]]}],
                      'layout':{'title':'留存客户数'}}}
    assert '1200' not in compose_answer_node(state)['answer']
