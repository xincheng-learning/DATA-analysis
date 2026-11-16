"""Declared scope checks are independent of candidate SQL and frozen answers."""
import io
from copy import deepcopy

import pytest


@pytest.fixture
def dataset(tmp_path):
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import validate_and_convert
    manager = DatasetManager()
    ds = manager.get_or_create('contract-test', tmp_path)
    for name, text in [('orders', 'order_id,status,amount\na,completed,10\nb,cancelled,90\nc,completed,30\n'),
                       ('values', 'record_id,value\n1,0\n2,2\n3,2\n4,5\n5,\n'),
                       ('lines', 'order_id,amount\na,1\na,2\nc,3\n')]:
        manager.add_file(ds.session_id, validate_and_convert(name+'.csv', io.BytesIO(text.encode()), tmp_path))
    yield ds
    manager.close_all()


def mean_plan():
    return {'source_quotes': ['只计算completed订单的平均amount'], 'stages': [
        {'name': 'summary', 'source': 'orders', 'filters': [
            {'field': 'orders.status', 'op': 'eq', 'value': 'completed'}],
         'select': [{'name': 'mean', 'op': 'mean', 'field': 'orders.amount'}]}], 'row_count': 1}


def test_paired_requirements_are_projected_without_losing_a_part():
    from app.schemas import AnalysisBrief
    brief = AnalysisBrief(goal='统计', parts=[
        {'requirement': '均值', 'criterion': {'answer_type': 'value', 'result_columns': ['mean']}},
        {'requirement': '单行', 'criterion': {'answer_type': 'constraint', 'subject': '单行', 'constraint_kind': 'output_shape'}}])
    result = brief.model_dump()
    assert result['requirements'] == ['均值', '单行']
    assert [s['requirement_index'] for s in result['acceptance']] == [0, 1]
    assert 'parts' not in result
    with pytest.raises(ValueError):
        AnalysisBrief(goal='冲突', requirements=['原要求'], acceptance=[
            {'requirement_index': 0, 'answer_type': 'value', 'result_columns': ['n']}],
            parts=[{'requirement': '替换', 'criterion': {'answer_type': 'value', 'result_columns': ['x']}}])


@pytest.mark.parametrize('part', [
    {'requirement': '', 'criterion': {'answer_type': 'explanation'}},
    {'requirement': 'x' * 1001, 'criterion': {'answer_type': 'explanation'}},
    {'requirement': '缺少配对标准'},
])
def test_paired_input_keeps_validation_at_the_model_boundary(part):
    from app.schemas import AnalysisBrief
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AnalysisBrief(goal='检查', parts=[part])


@pytest.mark.parametrize('repair_ok', [True, False])
def test_router_repairs_only_the_invalid_plan_once_and_preserves_the_request(dataset, monkeypatch, repair_ok):
    from types import SimpleNamespace
    from app.agent import router
    from app.calculation_contract import CalculationPlan
    from app import execution
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    bad = mean_plan()
    bad['stages'][0]['select'][0]['field'] = 'orders.missing'
    decision = router.RouteDecision(route='sql', reason='均值', analysis_brief={
        'goal': '均值', 'parts': [{'requirement': '计算均值', 'criterion': {
            'answer_type': 'value', 'result_columns': ['mean']}}], 'calculation_plan': bad})
    monkeypatch.setattr(router, '_get_router_llm', lambda: SimpleNamespace(invoke=lambda messages: decision))
    calls = []
    def repair(messages):
        calls.append(messages)
        return CalculationPlan.model_validate(mean_plan() if repair_ok else bad)
    monkeypatch.setattr(router, 'get_structured_llm', lambda schema: SimpleNamespace(invoke=repair))
    state = {'question': '只计算completed订单的平均amount', 'schema': 'orders(status,amount)', 'session_id': dataset.session_id}
    task = execution.AnalysisTask('qa', dataset.session_id)
    token = execution._current.set(task)
    try:
        result = router.router_node(state)
    finally:
        execution._current.reset(token)
    assert len(calls) == 1 and task.model_calls == 2
    assert result['analysis_brief']['requirements'] == ['计算均值']
    assert result['analysis_brief']['calculation_plan_attempts'][0]['plan'] == CalculationPlan.model_validate(bad).model_dump()
    assert result['analysis_brief']['calculation_plan_attempts'][0]['error']
    if repair_ok:
        assert result['route'] == 'sql' and not result.get('error')
        assert result['analysis_brief']['calculation_plan']['stages'][0]['select'][0]['field'] == 'orders.amount'
    else:
        assert result['route'] == 'refuse' and result['error'] and result['checks'][0]['status'] == 'failed'


def test_declared_scope_rejects_a_running_wrong_mean(dataset):
    from app.calculation_contract import verify_calculation
    brief = {'calculation_plan': mean_plan(), 'acceptance': [
        {'id': 'R1', 'requirement': '均值', 'result_columns': ['mean']}]}
    question = '只计算completed订单的平均amount'
    check = verify_calculation(dataset, brief, question, ['mean'], [{'mean': 20.0}], 1)
    assert check[0]['status'] == 'passed' and check[0]['requirement_ids'] == ['R1']
    with pytest.raises(ValueError, match='声明计算计划'):
        verify_calculation(dataset, brief, question, ['mean'], [{'mean': 130/3}], 1)


def test_plan_does_not_accept_unquoted_scope_or_unknown_columns(dataset):
    from app.calculation_contract import verify_calculation
    with pytest.raises(ValueError, match='用户原话'):
        verify_calculation(dataset, {'calculation_plan': mean_plan()}, '不是指定的原话', ['mean'], [{'mean': 20}], 1)
    bad = mean_plan()
    bad['stages'][0]['select'][0]['field'] = 'orders.not_present'
    with pytest.raises(ValueError, match='字段'):
        verify_calculation(dataset, {'calculation_plan': bad}, '只计算completed订单的平均amount', ['mean'], [{'mean': 20}], 1)


def test_null_zero_duplicates_and_full_rows_are_checked(dataset):
    from app.calculation_contract import verify_calculation
    plan = {'source_quotes': ['保留每条观测'], 'stages': [
        {'name': 'raw', 'source': 'values', 'select': [{'name': 'value', 'op': 'column', 'field': 'values.value'}],
         'order_by': [{'field': 'values.record_id'}]}], 'ordered_rows': True}
    rows = [{'value': v} for v in [0, 2, 2, 5, None]]
    assert verify_calculation(dataset, {'calculation_plan': plan}, '保留每条观测', ['value'], rows, 5)
    for bad in ([{'value': v} for v in [0, 2, 5, None]], [{'value': v} for v in [0, 2, 2, 5, 0]]):
        with pytest.raises(ValueError, match='声明计算计划'):
            verify_calculation(dataset, {'calculation_plan': plan}, '保留每条观测', ['value'], bad, len(bad))
    with pytest.raises(ValueError, match='完整'):
        verify_calculation(dataset, {'calculation_plan': plan}, '保留每条观测', ['value'], rows[:2], 5)
    with pytest.raises(ValueError, match='输出列'):
        verify_calculation(dataset, {'calculation_plan': plan}, '保留每条观测', ['value', 'value'], rows, 5)


def test_sql_node_blocks_wrong_result_before_saving_and_clears_stale_evidence(dataset, monkeypatch):
    from types import SimpleNamespace
    from app.agent.nodes import execute_sql_node
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    state = {'session_id': dataset.session_id, 'question': '只计算completed订单的平均amount',
             'sql': 'SELECT AVG(amount) AS mean FROM orders',
             'analysis_brief': {'calculation_plan': mean_plan(), 'acceptance': [{'id': 'R1', 'requirement': '均值', 'result_columns': ['mean']}]},
             'result_id': 'old', 'chart_spec': {'data': ['old']}}
    result = execute_sql_node(state)
    assert result['error'] and result['rows'] == [] and result['result_id'] is None and not result['chart_spec']
    state['sql'] += " WHERE status='completed'"
    correct = execute_sql_node(state)
    assert not correct['error'] and correct['rows'] == [{'mean': 20.0}]
    assert any(c['id'] == 'calculation_contract' for c in correct['checks'])


def test_python_wrong_result_enters_bounded_repair(dataset, monkeypatch):
    from types import SimpleNamespace
    from app.agent import nodes
    from app.tools.python_tool import PythonResult
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    monkeypatch.setattr('app.tools.python_tool.execute_python', lambda code, **kwargs: PythonResult(
        code=code, columns=['mean'], rows=[{'mean': 130/3}], output='wrong mean'))
    state = {'session_id': dataset.session_id, 'question': '只计算completed订单的平均amount', 'python_code': 'fixture',
             'analysis_brief': {'calculation_plan': mean_plan(), 'acceptance': [{'id': 'R1', 'requirement': '均值', 'result_columns': ['mean']}]}}
    result = nodes.execute_python_node(state)
    assert result['error'] and result['rows'] == [] and result['python_retryable']
    assert result['python_attempts'][0]['error'] and nodes.decide_after_python(result) == 'retry'


@pytest.mark.parametrize('engine', ['sql','python'])
def test_declared_readonly_plan_checks_input_integrity_even_if_router_omits_constraint(dataset, monkeypatch, engine):
    from types import SimpleNamespace
    from app.agent import nodes
    from app.tools.python_tool import PythonResult
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    monkeypatch.setattr('app.tools.python_tool.execute_python', lambda *args, **kwargs: PythonResult(code='fixture',columns=['mean'],rows=[{'mean':20.0}]))
    state={'session_id':dataset.session_id,'question':'只计算completed订单的平均amount',
        'sql':"SELECT AVG(amount) AS mean FROM orders WHERE status='completed'",'python_code':'fixture',
        'analysis_brief':{'calculation_plan':mean_plan(),'acceptance':[{'id':'R1','requirement':'均值','result_columns':['mean']}]}}
    execute=nodes.execute_sql_node if engine=='sql' else nodes.execute_python_node
    result=execute(state)
    assert not result.get('error')
    assert any(c['id']=='source_integrity' and c['status']=='passed' for c in result['checks'])
    signatures=iter(['before','changed'])
    monkeypatch.setattr('app.calculation_contract.source_fingerprint', lambda ds: next(signatures))
    failed=execute(state)
    assert failed['error'] and failed['rows']==[] and not failed['result_id']


def test_join_key_sides_resolve_unqualified_keys_without_guessing_other_fields(dataset):
    from app.calculation_contract import verify_calculation
    plan = {'source_quotes': ['汇总关联后的明细金额'], 'stages': [{'name': 'summary', 'source': 'orders',
        'joins': [{'table': 'lines', 'left_keys': ['order_id'], 'right_keys': ['order_id']}],
        'select': [{'name': 'amount', 'op': 'sum', 'field': 'lines.amount'}]}]}
    assert verify_calculation(dataset, {'calculation_plan': plan}, '汇总关联后的明细金额', ['amount'], [{'amount': 6}], 1)


def test_ratio_can_use_declared_same_stage_measures_without_extra_output(dataset):
    from app.calculation_contract import verify_calculation
    plan = {'source_quotes': ['计算完成占比'], 'stages': [{'name': 'summary', 'source': 'orders', 'select': [
        {'name': 'completed', 'op': 'count_if', 'condition': {'field': 'status', 'op': 'eq', 'value': 'completed'}},
        {'name': 'total', 'op': 'count_rows'},
        {'name': 'percent', 'op': 'ratio', 'numerator': 'completed', 'denominator': 'total', 'multiplier': 100}]}]}
    brief = {'calculation_plan': plan, 'acceptance': [{'id': 'R1', 'requirement': '占比', 'result_columns': ['percent']}]}
    assert verify_calculation(dataset, brief, '计算完成占比', ['percent'], [{'percent': 200/3}], 1)
    items = plan['stages'][0]['select']
    plan['stages'][0]['select'] = [items[2], items[0], items[1]]
    assert verify_calculation(dataset, brief, '计算完成占比', ['percent'], [{'percent': 200/3}], 1)
    with pytest.raises(ValueError, match='声明计算计划'):
        verify_calculation(dataset, brief, '计算完成占比', ['percent'], [{'percent': 100}], 1)


def test_cyclic_declared_ratios_are_rejected_without_executing_a_query(dataset):
    from app.calculation_contract import verify_calculation
    plan = {'source_quotes': ['循环引用'], 'stages': [{'name': 'bad', 'source': 'orders', 'select': [
        {'name': 'a', 'op': 'ratio', 'numerator': 'b', 'denominator': 'orders.amount'},
        {'name': 'b', 'op': 'ratio', 'numerator': 'a', 'denominator': 'orders.amount'}]}]}
    with pytest.raises(ValueError, match='循环'):
        verify_calculation(dataset, {'calculation_plan': plan}, '循环引用', ['a', 'b'], [], 0)


def test_invalid_plan_stops_before_generating_or_executing_candidate_code(dataset, monkeypatch):
    from types import SimpleNamespace
    from app.agent import nodes
    from app.agent.graph import decide_after_validator
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    monkeypatch.setattr(nodes, '_get_llm', lambda: pytest.fail('Invalid plans must not spend another model call on candidate code'))
    bad = mean_plan()
    bad['stages'][0]['select'][0]['field'] = 'orders.absent'
    state = {'session_id': dataset.session_id, 'question': '只计算completed订单的平均amount',
             'schema': dataset.schema_summary(), 'analysis_brief': {'calculation_plan': bad}}
    sql = nodes.write_sql_node(state)
    assert sql['error'] and sql['sql_retryable'] is False
    assert decide_after_validator(sql) == 'compose_answer'
    python = nodes.write_python_node(state)
    assert python['error'] and python['python_retryable'] is False


def test_unexecutable_aggregate_plan_is_caught_before_candidate_generation(dataset, monkeypatch):
    from types import SimpleNamespace
    from app.agent import nodes
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    monkeypatch.setattr(nodes, '_get_llm', lambda: pytest.fail('Invalid aggregate plans must not generate candidate SQL'))
    plan = mean_plan()
    plan['stages'][0]['select'].insert(0, {'name': 'status', 'op': 'column', 'field': 'orders.status'})
    state = {'session_id': dataset.session_id, 'question': '只计算completed订单的平均amount',
        'schema': dataset.schema_summary(), 'analysis_brief': {'calculation_plan': plan}}
    result = nodes.write_sql_node(state)
    assert result['error'] and result['sql_retryable'] is False


@pytest.mark.parametrize('broken', ['missing_output', 'row_count'])
def test_plan_preflight_rejects_an_impossible_requested_output(dataset, monkeypatch, broken):
    from types import SimpleNamespace
    from app.calculation_contract import request_output_columns
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    plan = mean_plan()
    if broken == 'row_count':
        plan['row_count'] = 2
    state = {'session_id': dataset.session_id, 'question': '只计算completed订单的平均amount', 'analysis_brief': {
        'calculation_plan': plan, 'acceptance': [{'id': 'R1', 'requirement': '均值', 'result_columns': ['not_in_plan'] if broken == 'missing_output' else ['mean']}]}}
    with pytest.raises(ValueError, match='声明计算计划'):
        request_output_columns(state)


def test_workflow_plan_checks_only_active_requirements_without_losing_other_parts(dataset, monkeypatch):
    from types import SimpleNamespace
    from app.calculation_contract import request_output_columns, verify_calculation
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: SimpleNamespace(get=lambda sid: dataset))
    brief = {'calculation_plan': mean_plan(), 'active_requirement_ids': ['R1'], 'acceptance': [
        {'id': 'R1', 'requirement': '本步均值', 'result_columns': ['mean']}, {'id': 'R2', 'requirement': '另一步', 'result_columns': ['another_step_value']}]}
    state = {'session_id': dataset.session_id, 'question': '只计算completed订单的平均amount', 'analysis_brief': brief}
    assert request_output_columns(state) == ['mean']
    checks = verify_calculation(dataset, brief, state['question'], ['mean'], [{'mean': 20.0}], 1)
    assert checks[0]['requirement_ids'] == ['R1'] and len(brief['acceptance']) == 2


def test_large_integer_results_are_compared_exactly(dataset, monkeypatch):
    import pandas as pd
    from app.calculation_contract import verify_calculation
    plan = mean_plan()
    plan['stages'][0]['select'] = [{'name': 'n', 'op': 'count_rows'}]
    monkeypatch.setattr(type(dataset), 'execute', lambda *args, **kwargs: pd.DataFrame({'n': [10_000_000_000]}))
    brief = {'calculation_plan': plan}
    assert verify_calculation(dataset, brief, '只计算completed订单的平均amount', ['n'], [{'n': 10_000_000_000}], 1)
    with pytest.raises(ValueError, match='声明计算计划'):
        verify_calculation(dataset, brief, '只计算completed订单的平均amount', ['n'], [{'n': 10_000_000_001}], 1)


@pytest.mark.parametrize('kind', ['left', 'cross'])
def test_keyless_join_is_only_allowed_to_a_single_row_aggregate_stage(dataset, kind):
    from app.calculation_contract import verify_calculation
    plan = {'source_quotes': ['组合两张单行汇总表'], 'row_count': 1, 'stages': [
        {'name': 'amounts', 'source': 'orders', 'select': [{'name': 'total', 'op': 'sum', 'field': 'amount'}]},
        {'name': 'counts', 'source': 'values', 'select': [{'name': 'n', 'op': 'count_rows'}]},
        {'name': 'result', 'source': 'amounts', 'joins': [{'table': 'counts', 'kind': kind, 'left_keys': [], 'right_keys': []}],
         'select': [{'name': 'ratio', 'op': 'ratio', 'numerator': 'amounts.total', 'denominator': 'counts.n'}]}]}
    assert verify_calculation(dataset, {'calculation_plan': plan}, '组合两张单行汇总表', ['ratio'], [{'ratio': 26}], 1)
    plan['stages'][-1]['joins'][0]['table'] = 'lines'
    with pytest.raises(ValueError, match='单行汇总'):
        verify_calculation(dataset, {'calculation_plan': plan}, '组合两张单行汇总表', ['ratio'], [{'ratio': 26}], 1)


def test_controlled_declared_plans_block_all_frozen_mutants_without_false_blocks():
    from scripts.check_reliability_canaries import run
    result = run(declared=True)
    assert result['product_gate_passed'] and result['product_blocked_wrong'] == 36
    assert result['product_blocked_correct'] == 0 and result['wrong_marked_answered'] == 0
    assert 'NOT model generated' in result['plan_source']


def test_saved_plan_replay_reports_both_false_blocks_and_escaped_errors(tmp_path):
    import json
    from scripts.check_reliability_canaries import run
    from evals.reliability_dataset import build_cases
    from evals.declared_plan_fixtures import declared_plan
    cases = tmp_path / 'cases'
    cases.mkdir()
    case = build_cases()[0]
    plan = declared_plan(case['family'], case['turns'][0]['question'])
    (cases / (case['id']+'.json')).write_text(json.dumps({'turns': [{'final': {
        'analysis_brief': {'calculation_plan': plan}}}]}), encoding='utf-8')
    result = run(plan_run=tmp_path)
    assert result['cases'] == 1 and result['raw_execution_success'] == 2
    assert result['product_blocked_wrong'] == 1 and result['product_blocked_correct'] == 0
    assert all(row['has_declared_plan'] for row in result['outcomes'])


def test_saved_plan_replay_cannot_pass_with_zero_cases(tmp_path):
    from scripts.check_reliability_canaries import run
    with pytest.raises(ValueError, match='没有可评分'):
        run(plan_run=tmp_path)
