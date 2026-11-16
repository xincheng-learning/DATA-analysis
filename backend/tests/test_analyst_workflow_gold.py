"""Validate the gold itself; these are not Agent capability tests."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from evals.analyst_workflow_dataset import build_cases, profiles, public_input, references, verify_references
from evals.gold_dataset import compare_rows


def test_eight_real_tasks_have_explicit_evidence_and_applicable_steps():
    cases = build_cases()
    assert [c['id'] for c in cases] == [f'A{i:02}' for i in range(1, 9)]
    assert all(c['required_evidence'] and len(c['turns']) >= 2 for c in cases)
    assert [c['id'] for c in cases if c['applicability']['cleaning']] == ['A06']
    assert [c['id'] for c in cases if c['applicability']['business_decision']] == ['A01', 'A06']
    assert all('allowed_routes' not in t for c in cases for t in c['turns'])
    facts = references()
    assert all(key in facts for c in cases for key in c['expected_fact_ids'])
    assert all(facts[key]['profile'] == c['profile'] for c in cases for key in c['expected_fact_ids'])


def test_reference_sql_matches_independent_record_arithmetic_and_hand_anchors():
    facts = verify_references()
    assert facts['feb_overview']['rows'] == [{'sales': 590, 'orders': 4, 'aov': 147.5}]
    assert facts['month_compare']['rows'][0]['delta'] == -125
    assert facts['feb_finance']['rows'] == [{'sales': 590, 'refund': 50, 'net_sales': 540}]
    assert facts['dirty_before']['rows'] == [{'orders': 3, 'known_amount': 100, 'missing_amount': 1}]
    assert facts['dirty_after']['rows'] == [{'orders': 4, 'known_amount': 150, 'missing_amount': 1}]
    targets = {r['region']: r for r in facts['apr_targets']['rows']}
    assert targets['华东']['target'] == 0 and targets['华东']['attainment_pct'] is None
    assert targets['华北']['target'] is None and targets['华北']['difference'] is None
    assert facts['quarter_repeat']['rows'][0]['buyers'] == 7
    assert sum(r['buyers'] for r in facts['channel_repeat']['rows']) == 8
    assert sum(r['buyers'] for r in facts['acquisition_repeat']['rows']) == 7


def test_gold_rejects_business_errors_without_requiring_an_exact_sql_or_route():
    facts = references()
    correct = facts['feb_finance']['rows']
    wrong = deepcopy(correct)
    wrong[0]['refund'] += 25  # includes a pending refund
    assert not compare_rows(wrong, correct)
    incomplete = deepcopy(facts['online_daily']['rows'])
    assert incomplete[2]['known_amount'] is None and incomplete[2]['recorded_cells'] == 0
    assert incomplete[3]['known_amount'] == 0 and incomplete[3]['recorded_cells'] == 1
    incomplete[2]['known_amount'] = 0
    assert not compare_rows(incomplete, facts['online_daily']['rows'])
    assert compare_rows(list(reversed(correct)), correct)


def test_questions_do_not_leak_answers_future_decisions_or_reference_code():
    for scenario in build_cases():
        sent = public_input(scenario, scenario['turns'][0])
        assert set(sent) == {'data_dictionary', 'briefing', 'message'}
        text = json.dumps(sent, ensure_ascii=False)
        assert 'reference_sql' not in text and 'expected_fact_ids' not in text
        assert scenario['id'] not in text
        assert scenario['turns'][1]['message'] not in text
    data = profiles()
    assert all('unit_cost' not in row for row in data['commerce']['items'])
    assert data['dirty']['orders'][1]['status'] == ' completed '


def test_reference_checks_detect_a_broken_teacher(monkeypatch):
    from evals import analyst_workflow_dataset as gold
    real = gold.references
    def broken(data=None):
        facts = real(data)
        facts['feb_overview']['rows'][0]['sales'] += 1
        return facts
    monkeypatch.setattr(gold, 'references', broken)
    with pytest.raises(AssertionError, match='feb_overview'):
        gold.verify_references()


def test_frozen_pack_and_actual_csv_are_consistent():
    from scripts.build_analyst_workflow_gold import check
    result = check()
    assert result['cases'] == 8 and result['live_model_called'] is False
