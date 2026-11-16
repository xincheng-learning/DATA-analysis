"""Report revisions retain delivery targets, not previous execution plans."""
from copy import deepcopy
import pytest
from app.answer_acceptance import prepare_brief


@pytest.fixture
def history():
    question = '统计2026年5月全渠道的月度金额、每日图和渠道表。'
    previous = {'requirements': ['月度金额', '每日图', '渠道表'], 'acceptance': [
        {'requirement_index': 0, 'answer_type': 'value', 'result_columns': ['amount']},
        {'requirement_index': 1, 'answer_type': 'chart', 'metric_ids': ['item_amount'],
         'result_columns': ['day', 'amount'], 'result_stage': 'old_daily'},
        {'requirement_index': 2, 'answer_type': 'table', 'result_columns': ['channel', 'amount'],
         'result_stage': 'old_channels'}],
        'calculation_plan': {'source_quotes': [question], 'stages': [{'name': 'old_daily'}]}}
    return [{'id': 1, 'role': 'user', 'content': question},
            {'id': 2, 'role': 'assistant', 'content': '原报告', 'metadata': {'analysis_brief': previous}}]


def current_brief():
    return {'requirements': ['月度金额'], 'acceptance': [
        {'requirement_index': 0, 'answer_type': 'value', 'result_columns': ['amount']}]}


@pytest.mark.parametrize('question', [
    '只看线上渠道，其他条件不变，更新这份简报。',
    '只看线上渠道，更新这份报告，不需要解释。',
    '改看六月，更新该份报告，不必重新清洗。',
    '不需要解释并更新这份报告。'])
def test_update_keeps_deliveries_despite_unrelated_restrictions(history, question):
    result = prepare_brief({'question': question}, current_brief(), messages=history)
    inherited = [s for s in result['acceptance'] if s.get('inherited_delivery')]
    assert [s['answer_type'] for s in inherited] == ['chart', 'table']
    assert all(s['request_quote'] == question and not s['result_stage'] for s in inherited)
    assert 'calculation_plan' not in result


def test_metric_only_deliveries_are_retained(history):
    history[-1]['metadata']['analysis_brief']['acceptance'][1].pop('result_columns')
    result = prepare_brief({'question': '更新这份报告。'}, current_brief(), messages=history)
    assert any(s.get('answer_type') == 'chart' and s['metric_ids'] == ['item_amount']
               for s in result['acceptance'])


@pytest.mark.parametrize(('restriction', 'expected'), [
    ('不要图', ['table']), ('不需要绘制图表', ['table']), ('不要表格', ['chart']),
    ('不要图表和表格', []), ('只要新指标', []), ('只要金额，不要图', [])])
def test_explicit_cancellations_only_remove_requested_deliveries(history, restriction, expected):
    result = prepare_brief({'question': f'更新这份报告，{restriction}。'}, current_brief(), messages=history)
    assert [s['answer_type'] for s in result['acceptance'] if s.get('inherited_delivery')] == expected


@pytest.mark.parametrize('state', [
    {'question': '只看线上渠道，其他条件不变。'}, {'question': '线上渠道总金额是多少？'},
    {'question': '为什么没有更新这份报告？'}, {'question': '不要更新这份报告，只看金额。'},
    {'question': '汇总本月', 'user_request': '更新这份报告。', 'internal_analysis': True}])
def test_non_updates_and_internal_tasks_do_not_inherit_deliveries(history, state):
    result = prepare_brief(state, current_brief(), messages=history)
    assert not any(s.get('inherited_delivery') for s in result['acceptance'])


def test_existing_chart_with_other_metric_does_not_satisfy_old_chart(history):
    brief = current_brief()
    brief['requirements'].append('每日退款图')
    brief['acceptance'].append({'requirement_index': 1, 'answer_type': 'chart',
                               'result_columns': ['day', 'amount'], 'metric_ids': ['refund_amount']})
    result = prepare_brief({'question': '更新这份报告。'}, brief, messages=history)
    assert any(s.get('inherited_delivery') and s.get('metric_ids') == ['item_amount']
               for s in result['acceptance'])


def test_repeated_preparation_does_not_duplicate_or_copy_old_plan(history):
    brief = current_brief()
    question = '改看2026年6月线上渠道，更新这份报告。'
    brief['calculation_plan'] = {'source_quotes': [question], 'stages': [
        {'name': 'new_monthly', 'source': 'orders',
         'filters': [{'field': 'orders.channel', 'op': 'eq', 'value': '线上'}],
         'select': [{'name': 'amount', 'op': 'sum', 'field': 'orders.amount'}]}]}
    original = deepcopy(brief)
    first = prepare_brief({'question': question}, brief, messages=history)
    second = prepare_brief({'question': question}, first, messages=history)
    assert len(second['acceptance']) == 3
    assert second['calculation_plan']['stages'] == original['calculation_plan']['stages']
    assert brief == original
    assert next(s for s in second['scope_sources'] if s['key'] == 'selected_period')['text'] == '2026年6月'


def test_existing_complete_delivery_targets_are_not_duplicated(history):
    brief = deepcopy(history[-1]['metadata']['analysis_brief'])
    brief.pop('calculation_plan')
    result = prepare_brief({'question': '更新这份报告。'}, brief, messages=history)
    assert len(result['acceptance']) == 3
    assert not any(s.get('inherited_delivery') for s in result['acceptance'])
