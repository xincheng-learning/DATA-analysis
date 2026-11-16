from copy import deepcopy

import pandas as pd
from test_product_workflow import product_app  # noqa: F401
from test_local_api import local_app  # noqa: F401

from app.insights import result_facts


def monthly():
    frame = pd.DataFrame({'month': ['2018-06', '2018-07'], 'item_amount_brl': [42481.28, 50588.49],
                          'delivered_orders': [287, 359]})
    return {'route': 'viz', 'question': '分析商品金额变化', 'sql': 'SELECT ...', 'data_version': 'v1',
            'rows': frame.to_dict('records'), 'columns': list(frame), 'row_count': 2,
            'facts': result_facts(frame, 'SELECT ...', 'v1'),
            'chart_spec': {'data': [{'type': 'bar', 'x': ['2018-06', '2018-07'], 'y': [42481.28, 50588.49]}]}}


def test_comparison_has_numbered_findings_reasons_and_computed_source_table_without_model_calls(monkeypatch):
    from app.report_presentation import build_analysis_report
    from app import analysis_report
    monkeypatch.setattr(analysis_report, 'invoke_model', lambda *args: (_ for _ in ()).throw(AssertionError('No extra model calls')))
    state = monthly(); before = deepcopy(state)
    report = build_analysis_report(state)
    assert report['findings'] and report['findings'][0]['reason']
    assert report['findings'][0]['source_keys'] == ['S1']
    table = report['sources'][0]['table']
    assert table['kind'] == 'comparison'
    amounts = next(row for row in table['rows'] if row['指标'] == '商品金额（BRL）')
    assert amounts['2018-06'] == 42481.28 and amounts['2018-07'] == 50588.49
    assert amounts['差额'] == 8107.21 and amounts['相对变化'] == '+19.08%'
    assert any(row['指标'] == '每单商品金额（BRL）' for row in table['rows'])
    assert '| 指标 | 2018-06 | 2018-07 | 差额 | 相对变化 |\n| ---' in report['answer']
    assert any('不证明' in card['text'] for card in report['evidence'] if card['kind'] == 'quantity_value')
    assert state == before


def test_workflow_report_keeps_failed_requirements_and_maps_each_success_to_its_actual_path():
    from app.report_presentation import build_analysis_report
    state = {'route': 'workflow', 'workflow': {'status': 'partial', 'steps': [
        {'id': 'trend', 'tool':'analyze', 'request': '趋势', 'status': 'succeeded', 'result': monthly()},
        {'id': 'failed', 'request': '退款分析', 'status': 'failed', 'error': '没有退款数据'},
    ]}}
    report = build_analysis_report(state)
    assert report['sources'][0]['path'] == ['workflow', 'steps', 0, 'result']
    assert any('退款分析' in warning and '未完成' in warning for warning in report['warnings'])
    assert all('S2' not in finding['source_keys'] for finding in report['findings'])


def test_unknown_and_zero_remain_distinct_and_large_results_are_labelled_as_preview():
    from app.report_presentation import build_analysis_report
    state = {'route': 'python', 'python_code': 'result = df', 'python_summary': '已保留未知，不填零。',
             'row_count': 100, 'columns': ['group', 'amount'], 'rows': [{'group': 'A', 'amount': 0}, {'group': 'B', 'amount': None}]}
    report = build_analysis_report(state)
    table = report['sources'][0]['table']
    assert table['rows'][0]['amount'] == 0 and table['rows'][1]['amount'] is None
    assert table['total_rows'] == 100 and table['shown_rows'] == 2
    assert 'Python' in report['sources'][0]['scope']
    assert '100' in report['answer'] and '未知' in report['answer']


def test_scalar_error_and_pending_cleaning_do_not_get_a_business_report():
    from app.report_presentation import build_analysis_report
    assert build_analysis_report({'route': 'sql', 'columns': ['n'], 'rows': [{'n': 4}], 'row_count': 1}) is None
    assert build_analysis_report({**monthly(), 'error': 'failed'}) is None
    assert build_analysis_report({'route': 'workflow', 'workflow': {'status': 'waiting', 'steps': []}}) is None


def test_method_explanations_keep_receipt_links_without_repeating_the_statistics_table():
    from app.report_presentation import build_analysis_report
    state={'route':'python','question':'统计与方法','python_code':'result = df','data_version':'v1',
        'columns':['n','mean'],'rows':[{'n':61,'mean':86.2}],'row_count':1,
        'analysis_report':{'status':'generated','findings':[
            {'text':'统计结果','kind':'observation','reason':'实际计算','evidence_ids':['S1.R1']},
            {'text':'检验假设','kind':'method','reason':'H0 为等于指定基准，H1 为不等于。','evidence_ids':['S1.E1']},
            {'text':'适用前提','kind':'method','reason':'独立性尚未检验。','evidence_ids':['S1.E1']}]}}
    report=build_analysis_report(state)
    assert report['answer'].count('| n | mean |')==1
    assert report['findings'][1]['kind']=='method' and report['findings'][1]['source_keys']==['S1']
    assert 'H0' in report['answer'] and '独立性尚未检验' in report['answer']


def test_two_period_table_does_not_drop_an_unknown_requested_metric():
    from app.report_presentation import build_analysis_report
    state=monthly();state['rows'][1]['delivered_orders']=None
    state['facts']=result_facts(pd.DataFrame(state['rows']),state['sql'],'v1')
    table=build_analysis_report(state)['sources'][0]['table']
    assert table['kind']=='result' and table['rows'][1]['delivered_orders'] is None


def test_ambiguous_average_alias_is_not_silently_relabelled_or_duplicated():
    from app.report_presentation import build_analysis_report
    state=monthly()
    for row in state['rows']:row['avg_item_amount_brl']=round(row['item_amount_brl']/row['delivered_orders'],2)
    state['columns'].append('avg_item_amount_brl');state['facts']=result_facts(pd.DataFrame(state['rows']),'SELECT ...','v1')
    table=build_analysis_report(state)['sources'][0]['table']
    assert len(table['rows'])==3 and any(row['指标']=='avg_item_amount（BRL）' for row in table['rows'])


def test_existing_verified_synthesis_keeps_each_reason_and_its_citations():
    from app.report_presentation import build_analysis_report
    step = {**monthly(), 'status': 'succeeded'}
    state = {'route': 'investigate', 'investigation': {'status': 'completed', 'steps': [step], 'synthesis': {
        'status': 'generated', 'findings': [{'text': '订单与金额的方向需要分别判断。',
            'reason': '商品金额与订单数量使用同一观察范围。', 'recommendation': '继续核对商品组合。',
            'evidence_ids': ['S1.F4']}], 'warnings': [], 'hypotheses': [], 'next_checks': []}}}
    report = build_analysis_report(state)
    finding = next(f for f in report['findings'] if f['title'] == '订单与金额的方向需要分别判断。')
    assert finding['reason'] == '商品金额与订单数量使用同一观察范围。'
    assert finding['recommendation'] == '继续核对商品组合。'
    assert finding['source_keys'] == ['S1']


def test_rejected_python_text_cannot_reenter_the_report_or_stream(monkeypatch):
    from types import SimpleNamespace
    from app import analysis_report,analysis_pipeline,execution
    from app.agent.nodes import compose_answer_node
    from app.report_presentation import build_analysis_report
    unverified='广告导致利润增加9999元，已证明干预有效。'
    state={'route':'python','question':'分析分组差异','python_code':'result = computed',
        'python_output':'RAW_TOOL_OUTPUT','python_summary':unverified,'data_version':'v1',
        'columns':['group','score','说明'],'rows':[{'group':'A','score':1,'说明':unverified},
            {'group':'B','score':2,'说明':unverified}],'row_count':2}
    monkeypatch.setattr(analysis_report,'get_structured_llm',lambda schema:SimpleNamespace(invoke=lambda messages:
        analysis_report.ReportPlan(findings=[{'text':unverified,'evidence_ids':['S1.R1']} ])))
    task=execution.AnalysisTask('qa','python-safe-report');token=execution._current.set(task)
    try:
        initial=compose_answer_node(state)
        delivered=analysis_pipeline.run_delivery({**state,**initial})
        report=build_analysis_report(delivered)
    finally:execution._current.reset(token)
    assert unverified not in initial['answer'] and unverified not in task.draft_answer
    assert all(unverified not in str(finding) for finding in report['findings'])
    assert delivered['python_summary']==unverified and delivered['rows']==state['rows']
    assert report['sources'][0]['table']['columns']==['group','score','说明']


async def test_actual_api_saves_the_report_and_its_evidence_paths(product_app,monkeypatch):
    from app import main
    client,_,_=product_app
    sid='report-contract'
    response=await client.post('/sessions/'+sid+'/upload',files={'files':('sales.csv',b'month,amount\n2018-06,42481.28\n2018-07,50588.49\n','text/csv')})
    assert response.status_code==200
    monkeypatch.setattr(main,'get_chat_llm',lambda:object())
    monkeypatch.setattr(main,'run_agent',lambda *args,**kwargs:(monthly(),sid))
    answer=await client.post('/ask',json={'session_id':sid,'question':'分析月度金额变化'})
    assert answer.status_code==200,answer.text
    report=answer.json()['analysis_report']
    assert report['version']==1 and report['sources'][0]['path']==[]
    saved=(await client.get('/sessions/'+sid)).json()['messages'][-1]
    assert saved['metadata']['analysis_report']==report and '### 1.' in saved['content']


def test_markdown_reuses_one_complete_table_and_one_citation_without_dropping_findings():
    from app.report_presentation import build_analysis_report
    state={'route':'viz','question':'多项解释','columns':['category','amount'],'rows':[{'category':'A','amount':1},{'category':'B','amount':3},{'category':'C','amount':2}],'row_count':3}
    state['analysis_report']={'status':'generated','coverage':[{'requirement':'变化','status':'answered','evidence_ids':['S1.R1']}],
        'findings':[{'text':'第一项','reason':'各指标分别核对','evidence_ids':['S1.R1']},
                    {'text':'第二项','reason':'同一来源的另一解释','evidence_ids':['S1.R1']}]}
    answer=build_analysis_report(state)['answer']
    assert '第一项' in answer and '第二项' in answer
    assert answer.count('- [S1.R1]')==1 and answer.count('| category | amount |')==1
