"""Small offline counterexamples for confirmed business calculations."""
from copy import deepcopy

import pandas as pd
import pytest

from app.business_contract import ContractError, compile_requests
from app.calculation_contract import CalculationPlan, compile_plan
from test_business_contract import inputs, run  # noqa: F401
from test_execution_basis_workflow import manager  # noqa: F401


def test_existing_unknown_partial_zero_and_absent_facts_are_distinct(manager):
    ds = manager({'jobs': [
        {'job_id': key, 'date': '2026-03-01', 'site': key} for key in 'ABCD'],
        'credits': [{'job_id': 'A', 'amount': None}, {'job_id': 'B', 'amount': 5},
                    {'job_id': 'B', 'amount': None}, {'job_id': 'C', 'amount': 0}]})
    quote = '抵扣没有匹配记录为0，已有记录全未知或部分未知时保留未知。'
    definitions = {'credit': {'id': 'credit', 'label': '抵扣', 'unit': '元', 'op': 'sum',
        'entity': {'table': 'jobs', 'keys': ['job_id'], 'date_field': 'date'},
        'fact_table': 'credits', 'fact_keys': ['job_id'], 'field': 'amount',
        'absence': 'zero', 'unknowns': 'require_complete', 'confirmation_quote': quote}}
    request = {'id': 'result', 'metrics': [{'metric_id': 'credit', 'column': 'credit'}],
        'group_by': [{'field': 'site', 'column': 'site'}]}
    frame, _, _ = run((ds, quote, definitions, request))
    values = frame.set_index('site')['credit']
    assert pd.isna(values['A']) and pd.isna(values['B'])
    assert values['C'] == 0 and values['D'] == 0


def test_null_group_key_matches_real_fact_and_preserves_unknown(manager):
    ds = manager({'jobs': [{'job_id': 'a', 'site': None}, {'job_id': 'b', 'site': 'B'}],
                  'credits': [{'job_id': 'a', 'amount': None}, {'job_id': 'outside', 'amount': 9}]})
    quote = '无抵扣记录为0，已有未知保持未知；缺失站点仍独立成组。'
    definitions = {'credit': {'id': 'credit', 'label': '抵扣', 'unit': '元', 'op': 'sum',
        'entity': {'table': 'jobs', 'keys': ['job_id']}, 'fact_table': 'credits',
        'fact_keys': ['job_id'], 'field': 'amount', 'absence': 'zero', 'confirmation_quote': quote}}
    request = {'id': 'result', 'metrics': [{'metric_id': 'credit', 'column': 'credit'}],
               'group_by': [{'field': 'site', 'column': 'site'}]}
    frame, _, _ = run((ds, quote, definitions, request))
    assert len(frame) == 2
    assert pd.isna(frame.loc[frame['site'].isna(), 'credit'].iloc[0])
    assert frame.loc[frame['site'] == 'B', 'credit'].iloc[0] == 0


def test_empty_entity_period_retains_declared_absence_semantics(inputs):
    request = deepcopy(inputs[3])
    request['period'] = {'start': '2026-04-01', 'end': '2026-05-01'}
    frame, _, _ = run(inputs, request=request)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert pd.isna(row['usage']) and row['credit'] == 0 and pd.isna(row['net'])


def test_static_formula_revision_and_period_revision_are_independent(inputs):
    definitions = deepcopy(inputs[2])
    definitions['usage']['left_field'] = 'rate'
    revised, _, _ = run(inputs, definitions=definitions)
    assert revised.iloc[0]['usage'] == 1725 and revised.iloc[0]['net'] == 1723
    request = deepcopy(inputs[3])
    request['period'] = {'start': '2026-02-01', 'end': '2026-03-01'}
    old_formula, _, _ = run(inputs, request=request)
    assert old_formula.iloc[0]['usage'] == 300 and old_formula.iloc[0]['net'] == 250
    original, _, _ = run(inputs)
    assert original.iloc[0]['usage'] == 65


def test_static_entity_definition_cannot_hide_analysis_period(inputs):
    definitions = deepcopy(inputs[2])
    for definition in definitions.values():
        if 'entity' in definition:
            definition['entity']['filters'] = [
                {'field': 'date', 'op': 'ge', 'value': '2026-03-01'}]
    with pytest.raises(ContractError, match='期间|日期'):
        run(inputs, definitions=definitions)


def test_compile_and_execution_do_not_mutate_caller_definitions_or_requests(inputs):
    ds, question, definitions, request = inputs
    definitions = deepcopy(definitions)
    requests = [deepcopy(request)]
    requests[0]['filters'] = [{'field': 'site', 'op': 'eq', 'value': 'A'}]
    confirmed = [question]
    snapshot = deepcopy((definitions, requests, confirmed))
    result = compile_requests(ds, definitions, requests, question, confirmed)
    result_snapshot = deepcopy(result)
    sql, _ = compile_plan(ds, CalculationPlan.model_validate(result['plan']), question,
                          confirmed, authorized_windows=result['windows'])
    assert ds.execute(sql, as_frame=True).iloc[0]['net'] == 23
    assert (definitions, requests, confirmed) == snapshot
    assert result == result_snapshot
