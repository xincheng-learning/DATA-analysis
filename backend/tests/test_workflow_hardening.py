"""Regressions from real analyst tasks, with changed values and no model calls."""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from app.calculation_contract import CalculationPlan, compile_plan, verify_calculation


@pytest.fixture
def data(tmp_path, monkeypatch):
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import store_dataframe
    manager = DatasetManager()
    ds = manager.get_or_create('hardening', tmp_path)
    for table, rows in {'orders': [{'order_id': 'a', 'status': 'completed'}], 'lines': [
        {'order_id': 'a', 'quantity': 2, 'unit_price': 7},
        {'order_id': 'a', 'quantity': 3, 'unit_price': 11},
        {'order_id': 'a', 'quantity': None, 'unit_price': 100}]}.items():
        manager.add_file(ds.session_id, store_dataframe(table+'.csv', pd.DataFrame(rows), tmp_path))
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: manager)
    yield ds
    manager.close_all()


def brief(product=False):
    question = '商品收入为quantity×unit_price合计；只统计completed订单，未知不补零。'
    item = {'name': 'income', 'op': 'sum_product', 'numerator': 'lines.quantity', 'denominator': 'lines.unit_price'} if product else {
        'name': 'income', 'op': 'sum', 'field': 'lines.quantity'}
    return question, {'requirements': ['商品收入'], 'acceptance': [
        {'id': 'R1', 'requirement_index': 0, 'requirement': '商品收入', 'answer_type': 'value',
         'request_quote': question, 'result_columns': ['income'], 'metric_ids': []}],
        'calculation_plan': {'source_quotes': [question], 'stages': [{'name': 'total', 'source': 'lines',
            'joins': [{'table': 'orders', 'left_keys': ['order_id'], 'right_keys': ['order_id']}],
            'filters': [{'field': 'orders.status', 'op': 'eq', 'value': 'completed'}], 'select': [item]}]}}


def test_confirmed_product_cannot_be_replaced_by_quantity_sum(data):
    question, b = brief()
    with pytest.raises(ValueError, match='公式'):
        verify_calculation(data, b, question, ['income'], [{'income': 5.0}], 1)


def test_product_sum_preserves_unknowns_and_returns_actual_money(data):
    question, b = brief(True)
    query, columns = compile_plan(data, CalculationPlan.model_validate(b['calculation_plan']), question)
    rows = data.execute(query, as_frame=True).to_dict('records')
    assert rows == [{'income': 47}]
    checks = verify_calculation(data, b, question, columns, rows, 1)
    assert any(c['id'] == 'user_formula' and c['status'] == 'passed' for c in checks)


def test_wrong_operand_and_wrong_plan_do_not_spend_a_sql_model_call(data, monkeypatch):
    from app.agent import nodes
    question, b = brief()
    monkeypatch.setattr(nodes, '_get_llm', lambda: pytest.fail('Invalid formula must stop before SQL generation'))
    result = nodes.write_sql_node({'session_id': data.session_id, 'question': question, 'schema': 'orders,lines', 'analysis_brief': b})
    assert result['sql_retryable'] is False and '公式' in result['error']


def test_wrong_product_operand_is_not_certified(data):
    question, b = brief(True)
    b['calculation_plan']['stages'][0]['select'][0]['denominator'] = 'lines.quantity'
    with pytest.raises(ValueError, match='公式'):
        verify_calculation(data, b, question, ['income'], [{'income': 13.0}], 1)


@pytest.mark.parametrize('product', [False, True])
def test_combined_requirement_with_english_aliases_still_requires_the_formula(data, product):
    question, b = brief(product)
    b['acceptance'][0].update(requirement='商品收入和订单数', result_columns=['income', 'order_count'])
    b['calculation_plan']['stages'][0]['select'].append({'name': 'order_count', 'op': 'count_rows'})
    query, columns = compile_plan(data, CalculationPlan.model_validate(b['calculation_plan']), question)
    rows = data.execute(query, as_frame=True).to_dict('records')
    if product:
        assert any(c['id'] == 'user_formula' for c in verify_calculation(data, b, question, columns, rows, 1))
    else:
        with pytest.raises(ValueError, match='公式'):
            verify_calculation(data, b, question, columns, rows, 1)


def test_explanation_and_constraint_do_not_disable_multigrain_split():
    from app.agent.workflow import calculation_workflow
    from test_chart_analysis_recovery import multigrain_brief
    b = multigrain_brief()
    for kind, text in [('explanation', '说明两种粒度的适用范围'), ('constraint', '不改原数据')]:
        index = len(b['acceptance'])
        b['requirements'].append(text)
        b['acceptance'].append({'id': f'R{index+1}', 'requirement_index': index, 'requirement': text,
            'answer_type': kind, 'subject': text, 'result_columns': [], 'metric_ids': []})
    original = deepcopy(b)
    plan = calculation_workflow(b)
    assert plan is not None and len(plan.steps) == 2
    assert set(i for s in plan.steps for i in s.covers) == {0, 1, 2, 3}
    assert b == original


def test_selected_period_is_saved_and_inherited_without_a_magic_followup_phrase():
    from app.answer_acceptance import prepare_brief
    first = '整理2026年4月各地区金额'
    base = {'goal': first, 'requirements': ['地区金额'], 'acceptance': [
        {'requirement_index': 0, 'answer_type': 'table', 'result_columns': ['region', 'amount'], 'request_quote': first}]}
    prepared = prepare_brief({'question': first}, base)
    assert any('2026年4月' in s['text'] for s in prepared['scope_sources'])
    messages = [{'id': 1, 'role': 'user', 'content': first}, {'id': 2, 'role': 'assistant', 'content': '执行失败', 'metadata': {'analysis_brief': prepared}}]
    followup = '哪些地区可以判断目标完成情况，给出对应依据'
    result = prepare_brief({'question': followup}, {**base, 'goal': followup}, messages=messages)
    assert any('2026年4月' in s['text'] and s['message_id'] == 1 for s in result['scope_sources'])
    reset = prepare_brief({'question': '取消时间筛选，查看所有月份'}, {**base, 'goal': '全部期间'}, messages=messages)
    assert not any(s['key'] == 'selected_period' for s in reset['scope_sources'])
    relative = prepare_brief({'question': '改看最近两个月，其他条件不变'}, base, messages=messages)
    assert [s['text'] for s in relative['scope_sources'] if s['key']=='selected_period'] == ['最近两个月']


def test_stage_token_budget_disables_only_automatic_code_reasoning(monkeypatch):
    from app import execution, model_settings
    from langchain_core.runnables import RunnableLambda
    from langchain_core.messages import AIMessage, HumanMessage
    seen = []
    def reply(messages, **kwargs):
        seen.append(kwargs)
        return AIMessage(content='SELECT 1')
    mode = {'base_url': 'https://api.deepseek.com', 'thinking_mode': 'auto'}
    monkeypatch.setattr(model_settings, 'get_model_status', lambda: mode)
    token = execution._current.set(execution.AnalysisTask('qa', 'hardening'))
    try:
        execution.checkpoint('write_sql')
        execution.invoke_model(RunnableLambda(reply), [HumanMessage(content='查订单数')])
        assert seen[-1]['max_tokens'] == 2048
        assert seen[-1]['extra_body']['thinking']['type'] == 'disabled'
        mode['thinking_mode'] = 'enabled'
        execution.invoke_model(RunnableLambda(reply), [HumanMessage(content='查订单数')])
        assert 'extra_body' not in seen[-1]
    finally:
        execution._current.reset(token)


def test_repeated_failed_sql_stops_instead_of_executing_again(data, monkeypatch):
    from app.agent import nodes
    question = '订单数'
    monkeypatch.setattr(nodes, '_get_llm', lambda: SimpleNamespace(invoke=lambda messages: SimpleNamespace(content='SELECT 1;')))
    state = {'question': question, 'schema': 'orders', 'previous_attempts': [{'sql': 'SELECT 1', 'error': '结果不匹配'}]}
    result = nodes.write_sql_node(state)
    assert result['sql_retryable'] is False and '重复' in result['error']


def test_sum_of_derived_row_product_and_operand_order_are_supported(data):
    question, b = brief(True)
    stage = b['calculation_plan']['stages'][0]
    stage['name'] = 'line_values'
    stage['select'] = [{'name': 'q', 'op': 'column', 'field': 'lines.quantity'},
        {'name': 'p', 'op': 'column', 'field': 'lines.unit_price'},
        {'name': 'line_amount', 'op': 'multiply', 'numerator': 'p', 'denominator': 'q'}]
    b['calculation_plan']['stages'].append({'name': 'total', 'source': 'line_values', 'select': [
        {'name': 'income', 'op': 'sum', 'field': 'line_values.line_amount'}]})
    query, columns = compile_plan(data, CalculationPlan.model_validate(b['calculation_plan']), question)
    rows = data.execute(query, as_frame=True).to_dict('records')
    assert rows == [{'income': 47}]
    assert any(c['id'] == 'user_formula' for c in verify_calculation(data, b, question, columns, rows, 1))


def test_explicit_formula_survives_followup_and_period_changes(data):
    from app.answer_acceptance import prepare_brief
    question, b = brief()
    first = prepare_brief({'question': question}, b, dataset=data)
    messages = [{'id': 1, 'role': 'user', 'content': question}, {'id': 2, 'role': 'assistant', 'metadata': {'analysis_brief': first}}]
    followup = '仍按刚才口径，更新商品收入'
    b['calculation_plan']['source_quotes'] = [followup]
    b['acceptance'][0]['request_quote'] = followup
    current = prepare_brief({'question': followup}, b, dataset=data, messages=messages)
    with pytest.raises(ValueError, match='公式'):
        verify_calculation(data, current, followup, ['income'], [{'income': 5}], 1)
    latest = '商品收入改为lines.unit_price合计，仅看4月'
    changed = prepare_brief({'question': latest}, b, dataset=data, messages=messages)
    assert not any(s['key'] == 'formula:商品收入' for s in changed['scope_sources'])


def test_actual_a05_bad_plan_is_blocked_and_corrected_plan_accepts_590(tmp_path):
    import json
    from pathlib import Path
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import validate_and_convert
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((Path(__file__).parent/'fixtures/workflow-a05-plan.json').read_text(encoding='utf-8'))
    manager = DatasetManager()
    ds = manager.get_or_create('actual-a05', tmp_path)
    try:
        for path in (root/'evals/analyst_workflow_gold_v1/data/commerce').glob('*.csv'):
            with path.open('rb') as stream:
                manager.add_file(ds.session_id, validate_and_convert(path.name, stream, tmp_path))
        rows = [{'商品销售额': 8.0, '已结退款额': 50.0, '退款后金额': -42.0}]
        with pytest.raises(ValueError, match='公式'):
            verify_calculation(ds, fixture['brief'], fixture['question'], list(rows[0]), rows, 1)
        b = deepcopy(fixture['brief'])
        b['calculation_plan']['stages'][0]['select'][0].update(op='sum_product', field='', numerator='items.quantity', denominator='items.unit_price')
        result = ds.execute(fixture['correct_sql'], as_frame=True)
        assert result.to_dict('records') == [{'商品销售额': 590, '已结退款额': 50, '退款后金额': 540}]
        checks = verify_calculation(ds, b, fixture['question'], list(result.columns), result.to_dict('records'), len(result), sql=fixture['correct_sql'])
        assert any(c['id'] == 'user_formula' and c['status'] == 'passed' for c in checks)
    finally:
        manager.close_all()


def test_sql_budget_reaches_actual_compatible_client_payload(monkeypatch):
    import json
    import httpx
    from langchain_core.messages import HumanMessage
    from app import execution, model_settings
    from app.llm import build_compatible_client
    observed = []
    def respond(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 0,
            'model': 'deepseek-flash', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'SELECT 1'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    monkeypatch.setattr(model_settings, 'get_model_status', lambda: {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'thinking_mode': 'auto'})
    token = execution._current.set(execution.AnalysisTask('qa', 'hardening'))
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            model = build_compatible_client('https://api.deepseek.com', 'offline-test-key', 'deepseek-flash', http_client=http)
            execution.checkpoint('write_sql')
            result = execution.invoke_model(model, [HumanMessage(content='查订单数')])
        assert result.content == 'SELECT 1'
        assert observed[0]['max_tokens'] == 2048 and observed[0]['thinking']['type'] == 'disabled'
    finally:
        execution._current.reset(token)
