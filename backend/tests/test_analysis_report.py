from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from app import analysis_report as report
from app.execution import AnalysisCancelled, ModelBudgetExceeded
from app.insights import result_facts


@pytest.fixture
def evidence():
    frames = [pd.DataFrame({'month': ['2018-05', '2018-06'], 'item_amount': [1000, 800]}),
              pd.DataFrame({'region': ['East', 'West'], 'change': [-150, -50]})]
    steps = []
    for index, frame in enumerate(frames):
        steps.append({'step_id': f'step-{index}', 'question': ['月度商品金额', '地区变化贡献'][index], 'status': 'succeeded',
                      'sql': 'SELECT ...', 'data_version': 'v1', 'rows': frame.to_dict('records'), 'columns': list(frame),
                      'row_count': len(frame), 'facts': result_facts(frame, 'SELECT ...', 'v1'), 'answer': '旧的一步说明'})
    return {'status': 'completed', 'reason': '模型原始规划文本', 'steps': steps}


def use_plan(monkeypatch, **changes):
    calls = []
    payload = {'findings': [{'text': '商品金额下降，主力地区的变化值得优先核对。', 'evidence_ids': ['S1.F4', 'S2.R1']}],
               'hypotheses': ['可能与商品供给或活动节奏有关，仍需额外数据核对。'],
               'next_checks': ['核对主力地区的订单量与客单价。'], **changes}
    def invoke(messages):
        calls.append(messages)
        return report.ReportPlan(**payload)
    monkeypatch.setattr(report, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=invoke))
    return calls


def test_report_joins_steps_and_inserts_only_program_values(evidence, monkeypatch):
    calls = use_plan(monkeypatch)
    before = deepcopy(evidence)
    generated = report.compose_report('商品金额为什么下降', evidence)
    assert generated['status'] == 'generated'
    assert '200' in generated['answer'] and '20' in generated['answer'] and '-150' in generated['answer']
    assert 'S1.F4' in generated['answer'] and 'S2.R1' in generated['answer']
    assert '待验证' in generated['answer'] and len(calls) == 1
    assert evidence == before


def test_reasons_and_recommendations_remain_evidence_scoped(evidence,monkeypatch):
    use_plan(monkeypatch,findings=[{'text':'商品金额下降。','evidence_ids':['S1.F4'],
        'reason':'由于广告失效导致下降。','recommendation':'优化后收入将增加999元。'}])
    generated=report.compose_report('分析',evidence)
    finding=next(f for f in generated['findings'] if f['text']=='商品金额下降')
    assert '1,000' in finding['reason'] and finding['recommendation']==''
    assert '广告失效' not in generated['answer'] and '999' not in generated['answer']


@pytest.mark.parametrize('values,expected', [([120,150],'上升'),([150,120],'下降'),([0,0],'持平')])
def test_comparison_direction_is_compiled_from_values_not_model_wording(evidence,monkeypatch,values,expected):
    frame=pd.DataFrame({'month':['2024-02','2024-03'],'amount_cny':values})
    evidence['steps']= [{**evidence['steps'][0],'rows':frame.to_dict('records'),'columns':list(frame),
        'facts':result_facts(frame,'SELECT ...','v1')}]
    use_plan(monkeypatch,findings=[{'text':'金额下降。','evidence_ids':['S1.F4']}])
    generated=report.compose_report('比较金额',evidence)
    assert generated['status']=='generated' and expected in generated['findings'][0]['text']
    assert generated['findings'][0]['text']!='金额下降。'


def test_a_correct_causal_limitation_survives_with_the_actual_direction(evidence,monkeypatch):
    use_plan(monkeypatch,findings=[{'text':'商品金额减少不能证明因果。','evidence_ids':['S1.F4']}])
    generated=report.compose_report('解释变化边界',evidence)
    assert generated['status']=='generated'
    assert '不能证明因果' in generated['findings'][0]['text']


def test_contribution_evidence_ranks_individuals_without_ranking_other_as_a_state(evidence):
    frame=pd.DataFrame({'row_type':['state','state','other_states'],'buyer_state':['PR','CE','其余州合计'],
                        'change_brl':[2261.45,-1797.19,7642.95]})
    evidence['steps'][1].update(rows=frame.to_dict('records'),columns=list(frame),row_count=3,
        facts=result_facts(frame,'SELECT ...','v1'))
    card=next(c for c in report.build_evidence(evidence) if c['kind']=='contribution')
    assert card['largest_individual']=='PR' and card['net_change']==8107.21
    assert card['other_change']==7642.95 and '不证明' in card['text']


def test_contribution_evidence_does_not_sum_unknown_changes(evidence):
    frame=pd.DataFrame({'row_type':['state','other_states'],'buyer_state':['PR','其余州合计'],
                        'change_brl':[2261.45,None]})
    evidence['steps'][1].update(rows=frame.to_dict('records'),columns=list(frame),row_count=2,
        facts=result_facts(frame,'SELECT ...','v1'))
    assert not any(c['kind']=='contribution' for c in report.build_evidence(evidence))


def test_contribution_does_not_invent_an_absent_other_bucket(evidence):
    frame=pd.DataFrame({'row_type':['state','state'],'buyer_state':['PR','CE'],'change_brl':[2261.45,-1797.19]})
    evidence['steps'][1].update(rows=frame.to_dict('records'),columns=list(frame),row_count=2,
        facts=result_facts(frame,'SELECT ...','v1'))
    assert not any(c['kind']=='contribution' for c in report.build_evidence(evidence))


@pytest.mark.parametrize('text,ids', [
    ('销售额减少九成。', ['S1.F4']),
    ('商品金额下降了999元。', ['S1.F4']),
    ('这证明营销活动导致下降。', ['S1.F4']),
    ('全国市场需求下降。', ['S1.F4']),
    ('商品金额下降。', ['S9.F1']),
])
def test_unverifiable_model_claims_fall_back_without_another_request(evidence, monkeypatch, text, ids):
    calls = use_plan(monkeypatch, findings=[{'text': text, 'evidence_ids': ids}])
    generated = report.compose_report('调查商品金额下降', evidence)
    assert generated['status'] == 'fallback'
    assert text not in generated['answer']
    assert '月度商品金额' in generated['answer'] and '地区变化贡献' in generated['answer']
    assert len(calls) == 1


def test_failed_and_budget_limited_investigations_keep_successful_evidence(evidence, monkeypatch):
    monkeypatch.setattr(report, 'get_structured_llm', lambda schema: pytest.fail('Must not call model after budget exhaustion'))
    evidence['status'] = 'budget_exhausted'
    evidence['steps'].append({'step_id': 'failed', 'status': 'failed', 'error': 'failed', 'rows': [{'fake': 999999}]})
    generated = report.compose_report('调查', evidence)
    assert generated['status'] == 'fallback'
    assert '999999' not in str(generated['evidence'])
    assert '预算' in generated['answer'] and '失败' in generated['answer']
    assert '月度商品金额' in generated['answer'] and '地区变化贡献' in generated['answer']


@pytest.mark.parametrize('error', [RuntimeError('offline'), ModelBudgetExceeded('no budget')])
def test_explanation_failure_keeps_results_and_does_not_retry(evidence, monkeypatch, error):
    calls = []
    def invoke(messages):
        calls.append(messages)
        raise error
    monkeypatch.setattr(report, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=invoke))
    generated = report.compose_report('调查', evidence)
    assert generated['status'] == 'fallback' and '800' in generated['answer']
    assert len(calls) == 1


def test_cancellation_is_not_swallowed_by_fallback(evidence, monkeypatch):
    def invoke(messages):
        raise AnalysisCancelled('stop')
    monkeypatch.setattr(report, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=invoke))
    with pytest.raises(AnalysisCancelled):
        report.compose_report('调查', evidence)


def test_partial_period_and_unknown_values_remain_visible_even_when_not_selected(evidence, monkeypatch):
    frame = pd.DataFrame({'month': ['2018-07', '2018-08'], 'item_amount': [100, None], 'is_partial_month': [False, True]})
    evidence['steps'][0].update(rows=frame.where(frame.notna(), None).to_dict('records'), facts=result_facts(frame, 'SELECT ...', 'v1'))
    use_plan(monkeypatch, findings=[{'text': '地区变化值得进一步核对。', 'evidence_ids': ['S2.R1']}])
    generated = report.compose_report('调查', evidence)
    assert '不完整' in generated['answer']
    assert '不能当零' in generated['answer']


def test_zero_start_and_zero_over_positive_denominator_are_not_confused(evidence, monkeypatch):
    frame = pd.DataFrame({'month': ['2018-05', '2018-06'], 'item_amount': [0, 100]})
    evidence['steps'][0].update(rows=frame.to_dict('records'), facts=result_facts(frame, 'SELECT ...', 'v1'))
    evidence['steps'][1]['rows'] = [{'eligible_customers': 2, 'repeat_customers': 0, 'repeat_rate': 0}]
    cards = report.build_evidence(evidence)
    change = next(card for card in cards if card['id'] == 'S1.F4')
    assert '变化率不适用' in change['text']
    row = next(card for card in cards if card['id'] == 'S2.R1')
    assert '=2' in row['text'] and '=0' in row['text'] and '未知' not in row['text']


def test_comparison_period_words_are_allowed_but_amount_claims_are_not(evidence, monkeypatch):
    use_plan(monkeypatch, findings=[{'text': '两月商品金额存在差异，订单量变化值得核对。', 'evidence_ids': ['S1.F4']}],
             next_checks=['比较两个月的客单价。'])
    generated = report.compose_report('比较两期商品金额', evidence)
    assert generated['status'] == 'generated'
    assert '8107.209999' not in generated['answer']


def test_change_display_formats_float_noise_without_changing_source_facts(evidence):
    fact = next(f for f in evidence['steps'][0]['facts'] if f.get('statistic') == 'change')
    fact.update(start_value=42481.2800000001, end_value=50588.49000000009)
    cards = report.build_evidence(evidence)
    change = next(card for card in cards if card['id'] == 'S1.F4')
    assert '8,107.21' in change['text'] and '8107.209999' not in change['text']
    assert fact['start_value'] == 42481.2800000001


def test_observed_dates_can_be_written_in_chinese_but_unseen_dates_cannot(evidence, monkeypatch):
    use_plan(monkeypatch, findings=[{'text': '六月商品金额低于五月，需结合地区变化核对。', 'evidence_ids': ['S1.F4']}],
             hypotheses=['六月可能有订单结构变化，尚未验证。'])
    assert report.compose_report('比较五月和六月', evidence)['status'] == 'generated'
    use_plan(monkeypatch, findings=[{'text': '九月商品金额下降。', 'evidence_ids': ['S1.F4']}])
    assert report.compose_report('比较五月和六月', evidence)['status'] == 'fallback'


def test_measured_result_row_counts_are_allowed_but_not_reused_as_amounts(evidence, monkeypatch):
    use_plan(monkeypatch, findings=[{'text': '结果仅2行，需核对查询范围。', 'evidence_ids': ['S1.R1']}])
    assert report.compose_report('调查', evidence)['status'] == 'generated'
    use_plan(monkeypatch, findings=[{'text': '金额仅2元。', 'evidence_ids': ['S1.R1']}])
    assert report.compose_report('调查', evidence)['status'] == 'fallback'
    use_plan(monkeypatch, findings=[{'text': '结果共25行。', 'evidence_ids': ['S1.R1']}])
    assert report.compose_report('调查', evidence)['status'] == 'fallback'


def test_complex_report_keeps_valid_findings_when_one_sentence_or_next_check_is_invalid(evidence, monkeypatch):
    findings = [{'text': text, 'evidence_ids':['S1.F4']} for text in [
        '商品金额下降。', '两月商品金额存在差异。', '地区变化值得核对。',
        '六月商品金额低于五月。', '商品金额下降了999元。']]
    calls = use_plan(monkeypatch, findings=findings, hypotheses=['销售减少999元可能来自活动变化。'],
                     next_checks=['核对客户结构。', '补齐2018-08-31后再比较。'])
    generated = report.compose_report('调查', evidence)
    assert generated['status'] == 'generated'
    assert len(generated['findings']) == 4 and len(calls) == 1
    assert generated['next_checks'] == ['核对客户结构。'] and not generated['hypotheses']
    assert '999' not in generated['answer'] and '2018-08-31' not in generated['answer']
    assert any('省略' in warning for warning in generated['warnings'])


def test_explicit_population_limitation_is_allowed_but_population_claim_still_fails(evidence, monkeypatch):
    use_plan(monkeypatch, findings=[{'text':'仅为当前样本，不能据此推断全平台走势。','evidence_ids':['S1.R1']}])
    assert report.compose_report('调查',evidence)['status']=='generated'
    use_plan(monkeypatch, findings=[{'text':'据此推断全平台走势下降。','evidence_ids':['S1.R1']}])
    assert report.compose_report('调查',evidence)['status']=='fallback'


def test_unverified_comparisons_and_magnitude_words_do_not_become_numeric_conclusions(evidence,monkeypatch):
    for text in ['订单量降幅大于商品金额降幅。','这说明金额减少主要来自订单数减少。','该州大幅收缩，但另一州降幅温和。']:
        use_plan(monkeypatch,findings=[{'text':text,'evidence_ids':['S1.F4']}])
        generated=report.compose_report('调查',evidence)
        assert generated['status']=='fallback' and text not in generated['answer']


def test_extrema_are_scoped_by_program_instead_of_repeating_population_claim(evidence,monkeypatch):
    text='East是当前导入样本中金额最高的地区。'
    use_plan(monkeypatch,findings=[{'text':text,'evidence_ids':['S2.R1','S2.R2']}])
    generated=report.compose_report('调查',evidence)
    assert generated['status']=='generated' and text not in generated['answer']
    assert '第2步返回的2行' in generated['answer']
    assert '-150' in generated['answer'] and '-50' in generated['answer']


def test_observed_last_date_does_not_certify_a_missing_calendar_day(evidence,monkeypatch):
    evidence['steps'][0]['rows']=[{'month':'2018-08','last_purchase':'2018-08-29'}]
    use_plan(monkeypatch,findings=[{'text':'8月最后记录早于月末，因此月份覆盖不完整。','evidence_ids':['S1.R1']}])
    generated=report.compose_report('调查',evidence)
    assert generated['status']=='generated'
    assert '因此月份覆盖不完整' not in generated['answer']
    assert '不能单凭无记录日期判定数据缺失' in generated['answer']
    assert '实际观测到的范围' not in generated['answer']


def test_core_metric_changes_survive_when_model_selects_only_dimensional_evidence(evidence,monkeypatch):
    use_plan(monkeypatch,findings=[{'text':'地区变化对照。','evidence_ids':['S2.R1','S2.R2']}])
    generated=report.compose_report('比较金额和地区变化',evidence)
    assert generated['status']=='generated'
    assert '1,000' in generated['answer'] and '800' in generated['answer']
    assert any('S1.F4' in finding['evidence_ids'] for finding in generated['findings'])


@pytest.mark.parametrize('amount_column',['item_amount','item_amount_brl'])
def test_order_volume_and_order_value_decomposition_survives_model_omission(evidence,monkeypatch,amount_column):
    frame=pd.DataFrame({'month':['2018-05','2018-06'],amount_column:[51181.69,42481.28],
                        'delivered_orders':[364,287],'item_amount_change':[None,-8700.41]})
    evidence['steps'][0].update(rows=frame.to_dict('records'),row_count=2,columns=list(frame),
                                facts=result_facts(frame,'SELECT ...','v1'))
    use_plan(monkeypatch,findings=[{'text':'地区对照。','evidence_ids':['S2.R1']}])
    generated=report.compose_report('调查商品金额变化',evidence)
    derived=next(card for card in generated['evidence'] if card['kind']=='quantity_value')
    assert '140.61' in derived['text'] and '148.02' in derived['text']
    assert '-11,112.16' in derived['text'] and '2,411.75' in derived['text'] and '-8,700.41' in derived['text']
    assert derived['id'] in {ref for finding in generated['findings'] for ref in finding['evidence_ids']}
    assert 'item_amount_change' in ' '.join(generated['warnings'])
    assert '已知小计不能当完整合计' not in generated['answer']


@pytest.mark.parametrize('count,checks',[(0,[]),(None,[]),(1.5,[]),
    (364,[{'id':'aggregate_coverage','status':'warning','missing_rows':1,'detail':'有缺失金额'}])])
def test_order_value_decomposition_rejects_unknown_denominators_and_partial_amounts(evidence,count,checks):
    frame=pd.DataFrame({'month':['2018-05','2018-06'],'item_amount':[51181.69,42481.28],'delivered_orders':[count,287]})
    evidence['steps'][0].update(rows=frame.to_dict('records'),row_count=2,columns=list(frame),checks=checks,
                                facts=result_facts(frame,'SELECT ...','v1'))
    assert not any(card['kind']=='quantity_value' for card in report.build_evidence(evidence))


@pytest.mark.parametrize('chart_type,fields,drawn',[
    ('heatmap',{'x_column':'region','y_column':'month','z_column':'amount'},['amount']),
    ('histogram',{'x_column':'amount'},['amount']),
    ('histogram',{'x_column':'amount','y_column':'orders'},['amount','orders']),
    ('scatter',{'x_column':'amount','y_column':'orders'},['amount','orders']),
])
def test_real_chart_measurements_reach_requirement_coverage(chart_type,fields,drawn):
    from app.answer_acceptance import assess_coverage
    from app.checks import verify_chart
    from app.tools.viz import ChartSpec, build_plotly_spec

    rows=[{'region':'East','month':'May','amount':120,'orders':3},
          {'region':'West','month':'June','amount':150,'orders':4}]
    chart=build_plotly_spec(ChartSpec(chart_type=chart_type,title='实际度量',x_label='横轴',y_label='纵轴',**fields),rows)
    check=verify_chart(chart,rows,len(rows))
    brief={'acceptance':[{'id':'R1','requirement':'画出所需度量','answer_type':'chart','result_columns':drawn}],
           'active_requirement_ids':['R1']}
    result={'steps':[{'status':'succeeded','step_id':'chart','rows':rows,'columns':list(rows[0]),
        'row_count':len(rows),'analysis_brief':brief,'chart_spec':chart,'checks':[check]}]}
    cards=report.build_evidence(result)
    assert next(c for c in cards if c['kind']=='chart_artifact')['columns']==drawn
    coverage_report={'evidence':cards,'coverage':[{'requirement_id':'R1','evidence_ids':['S1.V1']}]}
    assert assess_coverage(brief,coverage_report)[0]['status']=='answered'
    brief['acceptance'][0]['result_columns']=[*drawn,'undrawn']
    assert assess_coverage(brief,coverage_report)[0]['status']!='answered'
    brief['acceptance'][0]['result_columns']=drawn
    next(c for c in cards if c['kind']=='execution_receipt')['requirement_ids']=['R2']
    assert assess_coverage(brief,coverage_report)[0]['status']!='answered'


def test_method_parameters_are_quoted_from_request_or_code_without_promoting_summary_or_proving_branches(evidence,monkeypatch):
    evidence['steps']=evidence['steps'][:1]
    evidence['steps'][0].update(python_code='if False:\n    stats.ttest_1samp(x, popmean=0.0)',python_summary='popmean=999')
    brief={'goal':'说明均值相对指定基准的检验假设','requirements':['解释检验假设']}
    finding={'kind':'method','text':'均值检验的假设与适用条件','reason':'H0 为总体均值等于指定基准，H1 为不等于该基准；观测独立是适用条件。',
        'source_quotes':['popmean=0.0'],'requirement_ids':['R1'],'evidence_ids':['S1.E1']}
    use_plan(monkeypatch,findings=[finding],coverage=[{'requirement_index':0,'status':'answered','evidence_ids':['S1.E1']}])
    generated=report.compose_report(brief['goal'],evidence,brief)
    assert generated['status']=='generated'
    assert 'popmean=0.0' in generated['answer'] and 'H0' in generated['answer']
    assert '代码记录不证明每个分支实际执行' in generated['answer']
    finding['source_quotes']=['popmean=999']
    use_plan(monkeypatch,findings=[finding])
    rejected=report.compose_report(brief['goal'],evidence,brief)
    assert rejected['status']=='fallback' and 'popmean=999' not in rejected['answer']
    finding.update(source_quotes=['popmean=0.0'],reason='样本均值为999。')
    use_plan(monkeypatch,findings=[finding])
    assert report.compose_report(brief['goal'],evidence,brief)['status']=='fallback'
    finding.update(source_quotes=['popmean=999'],reason='原假设针对指定基准。')
    brief['goal']='按popmean=999作比较'
    use_plan(monkeypatch,findings=[finding])
    assert report.compose_report('实际用户要求与0比较',evidence,brief)['status']=='fallback'
    finding['source_quotes']=['与0比较']
    use_plan(monkeypatch,findings=[finding])
    assert report.compose_report('实际用户要求与0比较',evidence,brief)['status']=='generated'


@pytest.mark.parametrize('text,reason',[
    ('H0为总体均值等于指定基准，H1为不等于；基准取0。','这是双侧单样本检验。'),
    ('适用前提为观测独立且总体近似正态。','这两项前提未作检验，不能声称已满足。'),
    ('均值偏离0不能推断任何因果机制。','统计检验不推断因果，不能证明干预有效。'),
    ('H0为总体均值等于指定基准，H1为不等于。','实际代码参数为popmean=0.0，样本标准差使用ddof=1。'),
])
def test_verified_method_parameters_and_ordinary_causal_caveats_survive(text,reason):
    finding=report.Finding(kind='method',text=text,reason=reason,requirement_ids=['R1'],evidence_ids=['S1.E1'],source_quotes=['popmean=0.0'])
    cards={'S1.E1':{'kind':'execution_receipt','python_code':'stats.ttest_1samp(x,popmean=0.0)\nx.std(ddof=1)'}}
    assert report._method_finding(finding,{},cards,'解释检验假设')
    for bad in ('样本均值为999。','样本均值为0。','p值为0。','popmean=999。','ddof=0。','广告导致均值变化。'):
        assert report._method_finding(finding.model_copy(update={'text':bad}),{},cards,'解释检验假设') is None


@pytest.mark.parametrize('kind,extra,drawn',[
    ('box',{},['category','amount']),('area',{},['category','amount']),('circle',{},['category','amount']),
    ('bubble',{'x_column':'color','size_column':'size','color_column':'color'},['color','amount','size']),
    ('heatmap_marks',{'y_column':'region','size_column':'size','color_column':'color'},['category','region','size','color']),
    ('text_table',{'table_columns':['region','amount']},['region','amount']),
])
def test_tableau_chart_receipts_keep_every_encoded_measure_and_requirement(kind,extra,drawn):
    from app.tools.viz import ChartSpec,build_plotly_spec
    from app.checks import verify_chart
    from app.answer_acceptance import assess_coverage
    rows=[{'category':'A','region':'East','amount':10,'size':2,'color':3}]
    spec=ChartSpec(**{'chart_type':kind,'x_column':'category','y_column':'amount','title':'核对','x_label':'类别','y_label':'金额',**extra})
    chart=build_plotly_spec(spec,rows)
    brief={'acceptance':[{'id':'R1','requirement':'绘图','answer_type':'chart','result_columns':drawn}],'active_requirement_ids':['R1']}
    cards=report.build_evidence({'steps':[{'status':'succeeded','rows':rows,'columns':list(rows[0]),'row_count':1,
        'chart_spec':chart,'analysis_brief':brief,'checks':[verify_chart(chart,rows,1)]}]})
    card=next(card for card in cards if card['kind']=='chart_artifact')
    assert card['columns']==drawn
    checked={'evidence':cards,'coverage':[{'requirement_id':'R1','evidence_ids':['S1.V1']}]}
    assert assess_coverage(brief,checked)[0]['status']=='answered'
    brief['acceptance'][0]['result_columns']=[*drawn,'not_drawn']
    assert assess_coverage(brief,checked)[0]['status']!='answered'


def test_box_explanation_uses_actual_statistics_instead_of_model_figures():
    from app.tools.viz import ChartSpec,build_plotly_spec
    rows=[{'group':'A','value':v} for v in [1,2,3,4,5,100]]
    chart=build_plotly_spec(ChartSpec(chart_type='box',x_column='group',y_column='value',title='箱线',x_label='组',y_label='值'),rows)
    cards=report.build_evidence({'steps':[{'status':'succeeded','rows':rows,'row_count':6,'columns':['group','value'],'chart_spec':chart}]})
    by_id={c['id']:c for c in cards}
    finding=report.Finding(text='A的中位数999',reason='伪造数字999',evidence_ids=['S1.B1'],requirement_ids=['R1'])
    compiled=report._canonical_finding(finding,by_id)
    assert '999' not in compiled.text+compiled.reason and '中位数=3.5' in compiled.reason
    method=report.Finding(kind='method',text='须为1.5×IQR范围内最远观测',reason='Q1与Q3界定箱体',evidence_ids=['S1.B1'],requirement_ids=['R1'],source_quotes=['须为1.5×IQR内最远观测'])
    assert report._method_finding(method,{},by_id,'说明规则')
    assert report._method_finding(method.model_copy(update={'reason':'均值999'}),{},by_id,'说明规则') is None
    temporal=report.Finding(text='本次观测的月份范围有限',reason='期间覆盖受限',evidence_ids=['S1.B1'])
    assert report._canonical_finding(temporal,by_id) is None
    by_id['S1.B1']['columns'].append('month')
    assert report._canonical_finding(temporal,by_id)


def test_method_can_quote_actual_execution_checks_without_promoting_the_summary():
    cards={'S1.E1':{'kind':'execution_receipt','python_code':'result = computed',
        'checks':[{'id':'python_result','status':'passed','detail':'仅核对结果格式，不证明统计方法正确。'}]}}
    finding=report.Finding(kind='method',text='不能推断因果，也不能外推到真实业务总体。',
        reason='执行核对只涉及格式，不构成业务机制的证据。',requirement_ids=['R1'],evidence_ids=['S1.E1'],
        source_quotes=['仅核对结果格式，不证明统计方法正确。'])
    assert report._method_finding(finding,{},cards,'不能推断因果')
    assert report._method_finding(finding.model_copy(update={'source_quotes':['已证明检验前提成立']}),{},cards,'不能推断因果') is None


def test_formal_hypotheses_keep_the_verified_baseline_without_repeating_the_population_symbol():
    cards={'S1.E1':{'kind':'execution_receipt','python_code':'stats.ttest_1samp(x,popmean=0.0)'}}
    f=report.Finding(kind='method',text='H0为总体均值等于0，H1为不等于0，采用双侧检验。',
        reason='独立性与正态性是适用条件，尚未核对。',requirement_ids=['R1'],evidence_ids=['S1.E1'],source_quotes=['popmean=0.0'])
    assert report._method_finding(f,{},cards,'解释假设')
    for bad in ('H0为总体均值等于999，H1为不等于999。','H0为样本均值等于0。','p值为0。'):
        assert report._method_finding(f.model_copy(update={'text':bad}),{},cards,'解释假设') is None


def test_positional_ttest_baseline_has_the_same_provenance_as_a_keyword_parameter():
    cards={'S1.E1':{'kind':'execution_receipt','python_code':'stats.ttest_1samp(x, -0.5)'}}
    f=report.Finding(kind='method',text='H0为总体均值等于-0.5，H1为不等于-0.5。',reason='独立性为适用条件，未作核对。',
        requirement_ids=['R1'],evidence_ids=['S1.E1'],source_quotes=['stats.ttest_1samp(x, -0.5)'])
    assert report._method_finding(f,{},cards,'解释假设')
    assert report._method_finding(f.model_copy(update={'text':'H0为总体均值等于999。'}),{},cards,'解释假设') is None


def test_single_summary_row_does_not_create_redundant_minimum_and_maximum_cards():
    import pandas as pd
    from app.insights import result_facts
    rows=[{'n':61,'mean':86.2}]
    cards=report.build_evidence({'steps':[{'status':'succeeded','rows':rows,'row_count':1,'columns':['n','mean'],
        'facts':result_facts(pd.DataFrame(rows),'','v1')}]})
    assert any(card['kind']=='row' and '61' in card['text'] for card in cards)
    assert not any(card['kind'] in {'min','max'} for card in cards)
