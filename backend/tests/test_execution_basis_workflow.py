"""Workflow invariants across inventory, usage and observational datasets."""
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from app.calculation_contract import CalculationPlan, compile_plan


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from app.tools.dataset_manager import DatasetManager
    from app.tools.ingest import store_dataframe
    mgr = DatasetManager()
    def load(tables):
        ds = mgr.get_or_create('basis', tmp_path)
        for table, rows in tables.items():
            mgr.add_file(ds.session_id, store_dataframe(table+'.csv', pd.DataFrame(rows), tmp_path))
        return ds
    monkeypatch.setattr('app.tools.dataset_manager.get_dataset_manager', lambda: mgr)
    yield load
    mgr.close_all()


def statement(question, stages, columns):
    return {'requirements': ['本轮结果'], 'acceptance': [{'id': 'R1', 'requirement_index': 0,
        'requirement': '本轮结果', 'answer_type': 'table', 'request_quote': question, 'result_columns': columns}],
        'calculation_plan': {'source_quotes': [question], 'stages': stages}}


@pytest.mark.parametrize('domain,missing_zero', [('stock_movements', True), ('usage_events', True), ('sensor_readings', False)])
def test_absence_unknown_partial_and_zero_are_different_states(manager, domain, missing_zero):
    ds = manager({'entities': [{'key': k} for k in 'ABCDE'], domain: [
        {'key': 'B', 'value': None}, {'key': 'C', 'value': 5}, {'key': 'C', 'value': None},
        {'key': 'D', 'value': 0}, {'key': 'E', 'value': 2}, {'key': 'E', 'value': 3}]})
    question = '本次来源范围已确认；没有有效事件记录时合计为0，有记录但值未知时总额未知。' if missing_zero else '无读数不代表0；缺记录及部分读数未知时，总量未知。'
    aggregate = {'name': 'totals', 'source': domain, 'group_by': [domain+'.key'], 'select': [
        {'name': 'key', 'op': 'column', 'field': domain+'.key'},
        {'name': 'value', 'op': 'sum', 'field': domain+'.value', 'null_policy': 'require_complete'}]}
    output = {'name': 'result', 'source': 'entities', 'joins': [{'table': 'totals', 'kind': 'left', 'left_keys': ['key'], 'right_keys': ['key']}],
        'select': [{'name': 'key', 'op': 'column', 'field': 'entities.key'},
            {'name': 'value', 'op': 'zero_if_unmatched', 'numerator': 'totals.value', 'denominator': 'totals.key', 'policy_quote': question}
            if missing_zero else {'name': 'value', 'op': 'column', 'field': 'totals.value'}]}
    query, _ = compile_plan(ds, CalculationPlan.model_validate(statement(question, [aggregate, output], ['key', 'value'])['calculation_plan']), question)
    frame = ds.execute(query, as_frame=True).set_index('key')
    assert (frame.loc['A', 'value'] == 0) if missing_zero else pd.isna(frame.loc['A', 'value'])
    assert pd.isna(frame.loc['B', 'value']) and pd.isna(frame.loc['C', 'value'])
    assert frame.loc['D', 'value'] == 0 and frame.loc['E', 'value'] == 5


def test_known_subtotal_is_explicit_and_does_not_claim_complete_total(manager):
    ds = manager({'readings': [{'x': 7}, {'x': None}, {'x': 0}]})
    question = '仅汇报已知读数小计，不当完整总量。'
    stage = {'name': 'total', 'source': 'readings', 'select': [{'name': 'known', 'op': 'sum', 'field': 'readings.x', 'null_policy': 'known_subtotal'}]}
    query, _ = compile_plan(ds, CalculationPlan.model_validate(statement(question, [stage], ['known'])['calculation_plan']), question)
    assert ds.execute(query, as_frame=True).iloc[0]['known'] == 7


def test_missing_zero_needs_user_evidence_and_a_real_unmatched_join_key(manager):
    ds = manager({'base': [{'id': 1}], 'events': [{'id': 1, 'amount': None}]})
    question = '未知金额保持未知'
    stage = {'name': 'result', 'source': 'base', 'joins': [{'table': 'events', 'kind': 'left', 'left_keys': ['id'], 'right_keys': ['id']}],
        'select': [{'name': 'value', 'op': 'zero_if_unmatched', 'numerator': 'events.amount', 'denominator': 'events.id'}]}
    for policy, key in [('', 'events.id'), (question, 'events.amount')]:
        stage['select'][0].update(policy_quote=policy, denominator=key)
        with pytest.raises(ValueError):
            compile_plan(ds, CalculationPlan.model_validate(statement(question, [stage], ['value'])['calculation_plan']), question)


def test_fixed_basis_skips_sql_model_and_does_not_self_certify_independent_verification(manager, monkeypatch):
    from app.agent import nodes
    ds = manager({'energy': [{'power': 4, 'hours': 3}, {'power': 2, 'hours': 5}]})
    question = '能耗为power×hours合计'
    b = statement(question, [{'name': 'total', 'source': 'energy', 'select': [
        {'name': 'value', 'op': 'sum_product', 'numerator': 'energy.power', 'denominator': 'energy.hours'}]}], ['value'])
    b['requirements'] = ['能耗']; b['acceptance'][0]['requirement'] = '能耗'
    monkeypatch.setattr(nodes, '_get_llm', lambda: pytest.fail('Compiled analysis must not call SQL-writing model'))
    state = {'session_id': ds.session_id, 'question': question, 'schema': ds.schema_summary(), 'analysis_brief': b}
    state.update(nodes.write_sql_node(state))
    assert state['sql_retryable'] is False
    result = nodes.execute_sql_node(state)
    assert result['rows'] == [{'value': 22}]
    assert any(c['id'] == 'compiled_execution' and c['status'] == 'passed' for c in result['checks'])
    assert not any(c['id'] == 'calculation_contract' and c['status'] == 'passed' for c in result['checks'])
    changed = deepcopy(state)
    changed['analysis_brief']['calculation_plan']['stages'][0]['select'][0].update(op='sum', field='energy.power')
    refused = nodes.execute_sql_node(changed)
    assert refused['error'] and refused['sql_retryable'] is False
    changed = deepcopy(state)
    changed['sql'] = 'SELECT 999 AS value'
    assert nodes.execute_sql_node(changed)['error']
    changed = deepcopy(state)
    changed['analysis_context']['execution_basis']['data_version'] = 'outdated'
    assert nodes.execute_sql_node(changed)['error']


@pytest.mark.parametrize('empty,expected',[(True,0),(False,None)])
def test_empty_aggregate_policy_never_fills_an_existing_unknown(manager,empty,expected):
    ds=manager({'readings':[{'id':1,'value':None},{'id':2,'value':2}]})
    question='来源完整，无记录合计0；有未知值则总量未知。'
    stage={'name':'total','source':'readings','filters':[{'field':'readings.id','op':'eq','value':99 if empty else 1}],
        'select':[{'name':'value','op':'sum','field':'readings.value','null_policy':'require_complete',
            'empty_policy':'zero','policy_quote':question}]}
    query,_=compile_plan(ds,CalculationPlan.model_validate(statement(question,[stage],['value'])['calculation_plan']),question)
    value=ds.execute(query,as_frame=True).iloc[0]['value']
    assert pd.isna(value) if expected is None else value==expected


def test_derived_measure_cannot_bypass_a_declared_missing_record_policy(manager):
    ds=manager({'base':[{'id':1,'used':12}], 'adjustments':[{'id':2,'value':5}]})
    question='来源完整，无调整记录时调整量为0。'
    stage={'name':'result','source':'base','joins':[{'table':'adjustments','kind':'left','left_keys':['id'],'right_keys':['id']}],
        'select':[{'name':'used','op':'column','field':'base.used'},
            {'name':'adjusted','op':'zero_if_unmatched','numerator':'adjustments.value','denominator':'adjustments.id','policy_quote':question},
            {'name':'net','op':'subtract','numerator':'used','denominator':'adjustments.value'}]}
    plan=CalculationPlan.model_validate(statement(question,[stage],['used','adjusted','net'])['calculation_plan'])
    with pytest.raises(ValueError,match='派生指标'):
        compile_plan(ds,plan,question)
    plan.stages[0].select[-1].denominator='adjusted'
    query,_=compile_plan(ds,plan,question)
    assert ds.execute(query,as_frame=True).to_dict('records')==[{'used':12,'adjusted':0,'net':12}]


def test_empty_policy_cannot_use_synthetic_left_join_rows_as_source_records(manager):
    ds=manager({'meters':[{'id':1}], 'readings':[{'id':2,'value':5}]})
    question='来源完整，无读数记录总量为0，有未知读数总量未知。'
    stage={'name':'result','source':'meters','joins':[{'table':'readings','kind':'left','left_keys':['id'],'right_keys':['id']}],
        'group_by':['meters.id'],'select':[{'name':'id','op':'column','field':'meters.id'},
            {'name':'value','op':'sum','field':'readings.value','null_policy':'require_complete','empty_policy':'zero','policy_quote':question}]}
    plan=CalculationPlan.model_validate(statement(question,[stage],['id','value'])['calculation_plan'])
    with pytest.raises(ValueError,match='LEFT JOIN'):
        compile_plan(ds,plan,question)


def test_invalid_dates_can_be_excluded_without_mutating_source(manager):
    ds = manager({'observations': [{'date': '2026-04-01', 'value': 7}, {'date': 'bad-date', 'value': 99}]})
    question = '仅计算2026年4月可识别日期的已知值，坏日期不归月，保留原数据。'
    stages = [{'name': 'total', 'source': 'observations', 'filters': [
        {'field': 'observations.date', 'op': 'ge', 'cast': 'try_date', 'value': '2026-04-01'},
        {'field': 'observations.date', 'op': 'lt', 'cast': 'try_date', 'value': '2026-05-01'}],
        'select': [{'name': 'value', 'op': 'sum', 'field': 'observations.value'}]}]
    query, _ = compile_plan(ds, CalculationPlan.model_validate(statement(question, stages, ['value'])['calculation_plan']), question)
    assert ds.execute(query, as_frame=True).iloc[0]['value'] == 7
    assert ds.execute("SELECT COUNT(*) FROM observations WHERE date='bad-date'").fetchone()[0] == 1
